"""Scanning-pipeline med 3-lagers dubblettskydd.

Flöde per mail:
  1. Kolla att message_id inte redan finns i DB (lager 2).
  2. Hämta meddelandet, iterera bilagor.
  3. Validera PDF (magic bytes).
  4. Claude namnger filen.
  5. Kolla att (filnamn, datum) inte redan finns i DB (lager 3).
  6. Kolla att filnamnet inte redan finns i Drive-mappen.
  7. Ladda upp till Drive.
  8. Logga i DB (ProcessedMessage + SavedFile).
  9. Sätt etiketten 'Bezala-Klar' på mailet (lager 1).
"""

from __future__ import annotations

import logging
from datetime import datetime
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from app.db import session_scope
from app.models import ProcessedMessage, SavedFile, ScanRun
from app.services.ai_namer import FileNamer
from app.services.bezala_client import BezalaClient, BezalaError
from app.services.bezala_field_mapper import build_receipt_params
from app.services.drive_client import DriveClient
from app.services.gmail_client import Attachment, DONE_LABEL, GmailClient, GmailMessage
from app.services.html_pdf_converter import HtmlToPdfError, html_to_pdf
from app.services.pdf_validator import looks_like_pdf
from app.services.receipt_analyzer import (
    AnalyzerError,
    ReceiptAnalysis,
    ReceiptAnalyzer,
)
from app.services.link_extractor import extract_receipt_link
from app.services.settings_service import (
    build_gmail_query,
    load_settings,
    sender_matches_link_fetch,
    subject_matches_exclusion,
)

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    found: int = 0
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    notes: list[str] = None

    def __post_init__(self):
        if self.notes is None:
            self.notes = []


def _message_already_processed(db, message_id: str) -> bool:
    return (
        db.query(ProcessedMessage.id)
        .filter(ProcessedMessage.message_id == message_id)
        .first()
        is not None
    )


def _filename_already_saved(db, filename: str, date_str: str) -> bool:
    return (
        db.query(SavedFile.id)
        .filter(SavedFile.file_name == filename, SavedFile.file_date == date_str)
        .first()
        is not None
    )


def run_scan(max_results: int = 50) -> ScanResult:
    """Kör en scanning-omgång. Skapar en ScanRun-rad och returnerar resultatet."""

    result = ScanResult()

    with session_scope() as db:
        app_settings = load_settings(db)
        gmail_query = build_gmail_query(app_settings, done_label=DONE_LABEL)
        excluded_subjects = list(app_settings.exclude_subjects or [])
        ai_enabled = bool(app_settings.ai_naming_enabled)
        auto_upload = bool(app_settings.auto_upload_enabled)
        confidence_threshold = int(app_settings.confidence_threshold or 0)
        ai_min_confidence = int(app_settings.ai_min_confidence_to_save or 0)
        link_fetch_senders = list(app_settings.link_fetch_senders or [])
        html_to_pdf_enabled = bool(getattr(app_settings, "html_to_pdf_enabled", True))
        run = ScanRun(started_at=datetime.utcnow(), status="running")
        db.add(run)
        db.flush()
        run_id = run.id

    try:
        gmail = GmailClient()
    except Exception as exc:
        logger.exception("Gmail-klienten kunde inte initialiseras: %s", exc)
        _finalize_run(run_id, result, status="error", note=f"Gmail init: {exc}")
        raise

    try:
        drive = DriveClient()
    except Exception as exc:
        logger.exception("Drive-klienten kunde inte initialiseras: %s", exc)
        _finalize_run(run_id, result, status="error", note=f"Drive init: {exc}")
        raise

    namer = FileNamer()
    analyzer = ReceiptAnalyzer()
    use_ai = ai_enabled and analyzer.enabled
    if ai_enabled and not analyzer.enabled:
        logger.warning(
            "ai_naming_enabled=true men ANTHROPIC_API_KEY saknas — faller tillbaka på heuristisk namngivning."
        )

    bezala: BezalaClient | None = None
    bezala_metadata: dict = {"accounts": [], "cost_centers": [], "vat_rates": []}
    if auto_upload:
        try:
            bezala = BezalaClient()
            logger.info(
                "Bezala auto-upload aktivt (confidence_threshold=%d).",
                confidence_threshold,
            )
            # Hämta referensdata en gång per scan — inte per kvitto.
            bezala_metadata = fetch_bezala_metadata(bezala)
            logger.info(
                "Bezala metadata: accounts=%d cost_centers=%d vat_rates=%d",
                len(bezala_metadata["accounts"]),
                len(bezala_metadata["cost_centers"]),
                len(bezala_metadata["vat_rates"]),
            )
        except BezalaError as exc:
            logger.warning("Bezala-klienten kunde inte initialiseras: %s", exc)

    try:
        message_ids = gmail.list_candidate_message_ids(
            query=gmail_query, max_results=max_results
        )
    except Exception as exc:
        logger.exception("Kunde inte lista Gmail-meddelanden: %s", exc)
        _finalize_run(run_id, result, status="error", note=f"Gmail list: {exc}")
        raise

    result.found = len(message_ids)
    logger.info(
        "Scanning hittade %d kandidater (AI=%s, query: %s)",
        result.found,
        "on" if use_ai else "off",
        gmail_query,
    )

    for mid in message_ids:
        try:
            _process_one_message(
                mid, gmail, drive, namer, analyzer, bezala, result,
                excluded_subjects=excluded_subjects,
                use_ai=use_ai,
                auto_upload=auto_upload,
                confidence_threshold=confidence_threshold,
                ai_min_confidence=ai_min_confidence,
                link_fetch_senders=link_fetch_senders,
                html_to_pdf_enabled=html_to_pdf_enabled,
                bezala_metadata=bezala_metadata,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fel under bearbetning av %s: %s", mid, exc)
            result.errors += 1
            result.notes.append(f"{mid}: {exc}")
            _log_error(mid, str(exc))

    if bezala is not None:
        bezala.close()

    _finalize_run(run_id, result, status="ok")
    return result


def fetch_bezala_metadata(bezala: BezalaClient) -> dict:
    """Hämta Bezala-metadata (accounts, cost_centers, vat_rates). Sväljer
    fel per endpoint så vi returnerar det vi faktiskt fick — anroparen
    kan fatta beslut om mappningen är komplett nog."""
    def _safe(fn, label: str) -> list[dict]:
        try:
            return fn()
        except BezalaError as exc:
            logger.warning("Bezala metadata %s misslyckades: %s", label, exc)
            return []

    return {
        "accounts": _safe(bezala.list_accounts, "accounts"),
        "cost_centers": _safe(bezala.list_cost_centers, "cost_centers"),
        "vat_rates": _safe(bezala.list_vat_rates, "vat_rates"),
    }


def _attempt_bezala_upload(
    bezala: BezalaClient | None,
    analysis: ReceiptAnalysis | None,
    msg: GmailMessage,
    pdf_bytes: bytes,
    filename: str,
    *,
    auto_upload: bool,
    confidence_threshold: int,
    metadata: dict | None = None,
) -> tuple[str, str | None, str | None]:
    """Försök ladda upp till Bezala. Returnerar (status, transaction_id, error).

    Status: 'success' | 'failed' | 'pending'.

    Funktionen kallas bara för filer som faktiskt sparats till Drive — alltså
    kvitton. 'skipped' används aldrig här (reserverat för icke-kvitton som
    aldrig når detta steg). När auto-upload är av, eller AI-data saknas, eller
    confidence ligger under tröskeln, markeras raden 'pending' så användaren
    kan ladda upp manuellt via UI-knappen.
    """
    if not auto_upload:
        return "pending", None, None
    if analysis is None:
        # Ingen AI-data = kan inte auto-fylla transaktionen. Låt användaren
        # ladda upp manuellt via knappen.
        return "pending", None, None
    if bezala is None:
        return "failed", None, "Bezala-klient kunde inte initialiseras"
    if analysis.confidence < confidence_threshold:
        logger.info(
            "Bezala: confidence %d < tröskel %d — väntar på manuell upload (%s)",
            analysis.confidence, confidence_threshold, filename,
        )
        return "pending", None, None

    if analysis.amount is None or not analysis.date:
        logger.warning(
            "Bezala: saknar amount eller date för %s — kan inte bygga vat_lines",
            filename,
        )
        return "pending", None, "amount/date saknas för Bezala-upload"

    meta = metadata or {"accounts": [], "cost_centers": [], "vat_rates": []}
    params = build_receipt_params(
        file_name=filename,
        sender=msg.sender,
        vendor=analysis.vendor,
        category=analysis.category,
        amount=analysis.amount,
        currency=analysis.currency,
        receipt_date=analysis.date,
        subject=msg.subject,
        accounts=meta["accounts"],
        cost_centers=meta["cost_centers"],
        vat_rates=meta["vat_rates"],
    )
    # vat_lines=[] är OK — Bezala använder kontots default_vat_id
    # automatiskt. Vi fortsätter med upload.
    try:
        receipt = bezala.upload_receipt(
            filename=filename,
            pdf_bytes=pdf_bytes,
            description=params["description"],
            date=params["date"],
            amount=params["amount"],
            currency=params["currency"],
            vat_lines=params["vat_lines"],
            account_id=params.get("account_id"),
            cost_center_id=params.get("cost_center_id"),
            vendor=params.get("vendor"),
        )
        return "success", receipt.attachment_id, None
    except BezalaError as exc:
        logger.exception("Bezala-upload misslyckades för %s", filename)
        err = f"{exc}"
        if exc.body:
            err = f"{exc} | body={exc.body}"
        return "failed", None, err[:2000]


def _process_one_message(
    message_id: str,
    gmail: GmailClient,
    drive: DriveClient,
    namer: FileNamer,
    analyzer: ReceiptAnalyzer,
    bezala: BezalaClient | None,
    result: ScanResult,
    *,
    excluded_subjects: list[str] | None = None,
    use_ai: bool = False,
    auto_upload: bool = False,
    confidence_threshold: int = 0,
    ai_min_confidence: int = 0,
    link_fetch_senders: list[str] | None = None,
    html_to_pdf_enabled: bool = True,
    bezala_metadata: dict | None = None,
) -> None:
    with session_scope() as db:
        if _message_already_processed(db, message_id):
            result.skipped += 1
            logger.debug("Hoppar över redan bearbetat %s", message_id)
            return

    msg = gmail.fetch_message(message_id)

    # Länk-fetch-gren: avsändare matchar link_fetch_senders → ignorera
    # alla befintliga bilagor och leta i body:n efter kvitto-länk. Raden
    # sparas som 'needs_manual_download' och väntar på manuell /fetch-pdf.
    if link_fetch_senders and sender_matches_link_fetch(msg.sender, link_fetch_senders):
        link = extract_receipt_link(msg.body_text, msg.body_html)
        if link:
            try:
                with session_scope() as db:
                    db.add(
                        ProcessedMessage(
                            message_id=msg.message_id,
                            thread_id=msg.thread_id,
                            sender=msg.sender,
                            subject=msg.subject,
                            received_at=msg.received_at,
                            status="needs_manual_download",
                            pending_link=link,
                        )
                    )
                result.processed += 1
                logger.info(
                    "Link-fetch: sparade %s som needs_manual_download (%s)",
                    message_id, link,
                )
            except IntegrityError:
                result.skipped += 1
                logger.warning("Race: %s redan loggat — hoppar", message_id)
            # INTE mark_done — användaren ska kunna trigga hämtning.
        else:
            result.skipped += 1
            logger.info(
                "Link-fetch: ingen kvitto-länk hittades i %s — hoppar",
                message_id,
            )
            _log_skip(msg, reason="no_link")
            try:
                gmail.mark_done(message_id)
            except Exception:
                logger.exception("Kunde inte sätta etikett på %s", message_id)
        return

    if excluded_subjects and subject_matches_exclusion(msg.subject, excluded_subjects):
        result.skipped += 1
        logger.info("Hoppar över %s — ämnet matchar exkludering: %r", message_id, msg.subject)
        _log_skip(msg, reason="excluded_subject")
        try:
            gmail.mark_done(message_id)
        except Exception:
            logger.exception("Kunde inte sätta etikett på %s", message_id)
        return

    pdf_attachments = [
        a for a in msg.attachments if looks_like_pdf(a.filename, a.mime_type, a.data)
    ]

    if not pdf_attachments:
        # Mailet saknar PDF-bilaga. Två alternativ:
        #   a) html_to_pdf_enabled=False → original "no_pdf"-skip
        #   b) annars: konvertera mail-bodyn till PDF och behandla som vanlig bilaga
        if not html_to_pdf_enabled:
            result.skipped += 1
            logger.info("Inga giltiga PDF-bilagor i %s — markerar som klar", message_id)
            _log_skip(msg, reason="no_pdf")
            try:
                gmail.mark_done(message_id)
            except Exception:
                logger.exception("Kunde inte sätta etikett på %s", message_id)
            return

        logger.info(
            "HTML→PDF triggas för %s (sender=%r subject=%r html_len=%d text_len=%d)",
            message_id, msg.sender, msg.subject,
            len(msg.body_html or ""), len(msg.body_text or ""),
        )

        if not (msg.body_html or msg.body_text):
            result.skipped += 1
            logger.info("HTML→PDF: %s saknar både html och text — hoppar", message_id)
            _log_skip(msg, reason="no_content")
            try:
                gmail.mark_done(message_id)
            except Exception:
                logger.exception("Kunde inte sätta etikett på %s", message_id)
            return

        try:
            pdf_bytes = html_to_pdf(
                msg.body_html or None,
                plain_text_fallback=msg.body_text or None,
            )
        except HtmlToPdfError as exc:
            result.skipped += 1
            logger.warning(
                "HTML→PDF misslyckades för %s (sender=%r subject=%r): %s",
                message_id, msg.sender, msg.subject, exc,
            )
            _log_skip(msg, reason="html_pdf_failed")
            try:
                gmail.mark_done(message_id)
            except Exception:
                logger.exception("Kunde inte sätta etikett på %s", message_id)
            return

        synth_filename = (msg.subject or f"mail-{message_id}").strip() or f"mail-{message_id}"
        synth_filename = synth_filename.replace("/", "-").replace("\\", "-")[:200]
        if not synth_filename.lower().endswith(".pdf"):
            synth_filename = f"{synth_filename}.pdf"
        pdf_attachments = [
            Attachment(
                filename=synth_filename,
                mime_type="application/pdf",
                data=pdf_bytes,
            )
        ]
        logger.info(
            "HTML→PDF: konverterade %s → %s (%d bytes, sender=%r)",
            message_id, synth_filename, len(pdf_bytes), msg.sender,
        )

    saved_any = False
    any_non_receipt = False

    for att in pdf_attachments:
        analysis: ReceiptAnalysis | None = None
        if use_ai:
            try:
                analysis = analyzer.analyze(
                    attachment_bytes=att.data,
                    mime_type=att.mime_type,
                    original_filename=att.filename,
                    sender=msg.sender,
                    subject=msg.subject,
                    snippet=msg.snippet,
                    received_at=msg.received_at,
                )
            except AnalyzerError as exc:
                logger.exception("Claude-analys misslyckades för %s: %s", message_id, exc)
                result.errors += 1
                result.notes.append(f"{message_id}: AI-analys: {exc}")
                _log_error(message_id, f"AI-analys: {exc}")
                return

            logger.info(
                "AI-analys %s: is_receipt=%s confidence=%d%% category=%r vendor=%r",
                message_id, analysis.is_receipt, analysis.confidence,
                analysis.category, analysis.vendor,
            )

            if not analysis.is_receipt:
                any_non_receipt = True
                logger.info(
                    "Claude: %s är inte ett kvitto (confidence=%d) — hoppar utan logg",
                    message_id,
                    analysis.confidence,
                )
                continue

            # Låg confidence → SPARA INTE i DB. Samma beteende som
            # is_receipt=False: mark_done i Gmail + logga i scan_run.notes.
            if ai_min_confidence > 0 and analysis.confidence < ai_min_confidence:
                any_non_receipt = True
                result.notes.append(
                    f"{message_id}: låg confidence {analysis.confidence}% < {ai_min_confidence}% — sparas ej"
                )
                logger.info(
                    "Filtrerat av AI-tröskel: %s confidence=%d%% < %d%% (sender=%r subject=%r)",
                    message_id,
                    analysis.confidence,
                    ai_min_confidence,
                    msg.sender,
                    msg.subject,
                )
                continue

        if analysis is not None:
            new_name = analysis.filename
        else:
            new_name = namer.name_for(
                sender=msg.sender,
                subject=msg.subject,
                snippet=msg.snippet,
                received_at=msg.received_at,
                original_filename=att.filename,
            )

        date_str = (msg.received_at or datetime.utcnow()).strftime("%Y%m%d")

        with session_scope() as db:
            if _filename_already_saved(db, new_name, date_str):
                result.skipped += 1
                logger.info("Dubblett (filnamn+datum): %s", new_name)
                continue

        if drive.filename_exists(new_name):
            result.skipped += 1
            logger.info("Filnamn finns redan i Drive: %s", new_name)
            continue

        upload = drive.upload_pdf(new_name, att.data)

        bezala_status, bezala_txn_id, bezala_err = _attempt_bezala_upload(
            bezala, analysis, msg, att.data, new_name,
            auto_upload=auto_upload,
            confidence_threshold=confidence_threshold,
            metadata=bezala_metadata,
        )

        try:
            with session_scope() as db:
                db.add(
                    ProcessedMessage(
                        message_id=msg.message_id,
                        thread_id=msg.thread_id,
                        sender=msg.sender,
                        subject=msg.subject,
                        received_at=msg.received_at,
                        file_name=new_name,
                        drive_file_id=upload.file_id,
                        drive_link=upload.web_view_link,
                        status="saved",
                        vendor=analysis.vendor if analysis else None,
                        amount=analysis.amount if analysis else None,
                        currency=analysis.currency if analysis else None,
                        receipt_date=analysis.date if analysis else None,
                        category=analysis.category if analysis else None,
                        summary=analysis.summary if analysis else None,
                        ai_confidence=analysis.confidence if analysis else None,
                        bezala_transaction_id=bezala_txn_id,
                        bezala_upload_status=bezala_status,
                        bezala_error_message=bezala_err,
                    )
                )
                db.add(
                    SavedFile(
                        file_name=new_name,
                        file_date=date_str,
                        drive_file_id=upload.file_id,
                    )
                )
            saved_any = True
            result.processed += 1
        except IntegrityError:
            logger.warning("Race: %s redan loggat — hoppar", msg.message_id)
            result.skipped += 1
            break

    if saved_any or any_non_receipt:
        try:
            gmail.mark_done(message_id)
        except Exception:
            logger.exception("Kunde inte sätta etikett på %s efter bearbetning", message_id)
    if any_non_receipt and not saved_any:
        result.skipped += 1


def _log_skip(msg: GmailMessage, reason: str) -> None:
    try:
        with session_scope() as db:
            db.add(
                ProcessedMessage(
                    message_id=msg.message_id,
                    thread_id=msg.thread_id,
                    sender=msg.sender,
                    subject=msg.subject,
                    received_at=msg.received_at,
                    status=f"skipped:{reason}",
                )
            )
    except IntegrityError:
        pass


def _log_error(message_id: str, error: str) -> None:
    try:
        with session_scope() as db:
            existing = (
                db.query(ProcessedMessage)
                .filter(ProcessedMessage.message_id == message_id)
                .first()
            )
            if existing:
                existing.status = "error"
                existing.error_message = error[:2000]
            else:
                db.add(
                    ProcessedMessage(
                        message_id=message_id,
                        status="error",
                        error_message=error[:2000],
                    )
                )
    except Exception:
        logger.exception("Kunde inte logga fel för %s", message_id)


def _finalize_run(run_id: int, result: ScanResult, *, status: str, note: str | None = None) -> None:
    try:
        with session_scope() as db:
            run = db.query(ScanRun).filter(ScanRun.id == run_id).first()
            if not run:
                return
            run.finished_at = datetime.utcnow()
            run.messages_found = result.found
            run.messages_processed = result.processed
            run.messages_skipped = result.skipped
            run.errors = result.errors
            run.status = status
            if note:
                result.notes.append(note)
            if result.notes:
                run.notes = "\n".join(result.notes)[:4000]
    except Exception:
        logger.exception("Kunde inte uppdatera ScanRun %s", run_id)
