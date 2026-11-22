"""C47 Del 1 + 2 — kvitton in från mobilen och från +kvitto-mail.

Samma kedja som Gmail-bilagor: AI-tolkning → Drive → rad i
processed_messages → matchning mot kortrad (waiting_receipts). Källa
(upload_source) = 'mobile' eller 'mail_kvitto'.

Inget skickas för attest. Inga skrivningar till Netvisor.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import ProcessedMessage

logger = logging.getLogger(__name__)

SOURCE_MOBILE = "mobile"
SOURCE_MAIL_KVITTO = "mail_kvitto"

ALLOWED_MIMES = {"application/pdf", "image/jpeg", "image/png"}
MAX_BYTES = 15 * 1024 * 1024


class InboxError(ValueError):
    """Fel som visas för användaren."""


def sniff_mime(data: bytes, fallback: str = "") -> str:
    if data[:4] == b"%PDF":
        return "application/pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return (fallback or "").lower()


def _ext(mime: str) -> str:
    return {"application/pdf": ".pdf", "image/png": ".png"}.get(mime, ".jpg")


@dataclass
class IngestResult:
    row: ProcessedMessage
    created: bool


def ingest_receipt_file(
    db: Session,
    data: bytes,
    *,
    filename: str,
    declared_mime: str = "",
    source: str,
    sender: str,
    subject: str | None = None,
    received_at: datetime | None = None,
    message_id: str | None = None,
    thread_id: str | None = None,
    analyzer=None,
    drive=None,
) -> IngestResult:
    """Tolka + spara ett kvitto. Idempotent på message_id. Höjer InboxError
    för fel filtyp/storlek. Raden får waiting_status='waiting_for_transaction'
    tills den kopplas till en kortrad."""
    from app.services.receipt_analyzer import AnalyzerError, ReceiptAnalyzer
    from app.services.receipt_dates import FLAG_DATE_CORRECTED, sanitize_receipt_date

    if message_id:
        existing = (
            db.query(ProcessedMessage)
            .filter(ProcessedMessage.message_id == message_id)
            .first()
        )
        if existing is not None:
            return IngestResult(existing, False)
    if not data:
        raise InboxError("Empty file")
    if len(data) > MAX_BYTES:
        raise InboxError(f"File too large ({len(data) / 1048576:.1f} MB, max 15 MB)")
    mime = sniff_mime(data, declared_mime)
    if mime not in ALLOWED_MIMES:
        raise InboxError("Only PDF, JPG or PNG receipts are supported")

    name = (filename or "kvitto").strip().replace("/", "-").replace("\\", "-")
    if not name.lower().endswith((".pdf", ".jpg", ".jpeg", ".png")):
        name += _ext(mime)
    now = datetime.utcnow()
    received = received_at or now

    analyzer = analyzer or ReceiptAnalyzer()
    analysis = None
    if getattr(analyzer, "enabled", False):
        try:
            analysis = analyzer.analyze(
                attachment_bytes=data, mime_type=mime, original_filename=name,
                sender=sender, subject=subject or name, snippet=subject or "",
                received_at=received,
            )
            if analysis.filename:
                # Bilden sparas som bild — AI:n föreslår alltid .pdf.
                base = analysis.filename.rsplit(".", 1)[0]
                name = base + _ext(mime) if mime != "application/pdf" else analysis.filename
        except AnalyzerError:
            logger.exception("[c47-inbox] AI-analys misslyckades för %s", name)

    if drive is None:
        from app.services.drive_client import DriveClient

        drive = DriveClient()
    up = drive.upload_attachment(name, data, mime)

    receipt_date = analysis.date if analysis else None
    flagged = bool(getattr(analysis, "date_corrected", False)) if analysis else False
    if receipt_date:
        receipt_date, fixed_now = sanitize_receipt_date(
            receipt_date, received, analysis.category if analysis else None,
        )
        flagged = flagged or fixed_now
    row = ProcessedMessage(
        message_id=message_id or f"{source}:{secrets.token_hex(8)}",
        thread_id=thread_id,
        sender=sender,
        subject=(subject or name)[:1000],
        received_at=received,
        file_name=name,
        drive_file_id=up.file_id,
        drive_link=up.web_view_link,
        status="saved",
        vendor=analysis.vendor if analysis else None,
        amount=analysis.amount if analysis else None,
        currency=analysis.currency if analysis else None,
        receipt_date=receipt_date,
        category=analysis.category if analysis else None,
        summary=analysis.summary if analysis else None,
        ai_description_en=analysis.description_en if analysis else None,
        ai_confidence=analysis.confidence if analysis else None,
        bezala_upload_status="pending",
        payment_method="company_card",
        upload_source=source,
        manual_uploaded_at=now if source == SOURCE_MOBILE else None,
        waiting_status="waiting_for_transaction",
        waiting_since=now,
        review_flag=FLAG_DATE_CORRECTED if flagged else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "[c47-inbox] source=%s id=%s vendor=%s amount=%s %s date=%s",
        source, row.id, row.vendor, row.amount, row.currency, row.receipt_date,
    )
    return IngestResult(row, True)
