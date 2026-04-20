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
from app.services.drive_client import DriveClient
from app.services.gmail_client import DONE_LABEL, GmailClient, GmailMessage
from app.services.pdf_validator import looks_like_pdf
from app.services.receipt_analyzer import (
    AnalyzerError,
    ReceiptAnalysis,
    ReceiptAnalyzer,
)
from app.services.settings_service import (
    build_gmail_query,
    load_settings,
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
                mid, gmail, drive, namer, analyzer, result,
                excluded_subjects=excluded_subjects,
                use_ai=use_ai,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fel under bearbetning av %s: %s", mid, exc)
            result.errors += 1
            result.notes.append(f"{mid}: {exc}")
            _log_error(mid, str(exc))

    _finalize_run(run_id, result, status="ok")
    return result


def _process_one_message(
    message_id: str,
    gmail: GmailClient,
    drive: DriveClient,
    namer: FileNamer,
    analyzer: ReceiptAnalyzer,
    result: ScanResult,
    *,
    excluded_subjects: list[str] | None = None,
    use_ai: bool = False,
) -> None:
    with session_scope() as db:
        if _message_already_processed(db, message_id):
            result.skipped += 1
            logger.debug("Hoppar över redan bearbetat %s", message_id)
            return

    msg = gmail.fetch_message(message_id)

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
        result.skipped += 1
        logger.info("Inga giltiga PDF-bilagor i %s — markerar som klar", message_id)
        _log_skip(msg, reason="no_pdf")
        try:
            gmail.mark_done(message_id)
        except Exception:
            logger.exception("Kunde inte sätta etikett på %s", message_id)
        return

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

            if not analysis.is_receipt:
                any_non_receipt = True
                logger.info(
                    "Claude: %s är inte ett kvitto (confidence=%d) — hoppar utan logg",
                    message_id,
                    analysis.confidence,
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
