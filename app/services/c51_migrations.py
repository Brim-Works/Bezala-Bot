"""C51 — läs momsen från kvitton som redan finns (en gång, i bakgrunden).

Kvitton som sparats före C51 saknar `vat_info`. För öppna kvitton (inte
kopplade, eller väntar på kortrad) från de senaste ~90 dagarna läses PDF:en
från Drive och kvittoanalysen körs igen — BARA `vat_info` sparas, inget
annat på raden ändras och ingenting skrivs till Bezala.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import MaintenanceTask, ProcessedMessage

logger = logging.getLogger(__name__)

TASK = "c51_vat_backfill_v1"
MAX_ROWS = 80


def _once(db: Session, name: str) -> bool:
    if db.query(MaintenanceTask).filter(MaintenanceTask.name == name).first():
        return False
    db.add(MaintenanceTask(name=name))
    db.commit()
    return True


def candidates(db: Session, now: datetime | None = None) -> list[ProcessedMessage]:
    now = now or datetime.utcnow()
    since = (now - timedelta(days=90)).date().isoformat()
    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(ProcessedMessage.vat_info.is_(None))
        .filter(ProcessedMessage.drive_file_id.isnot(None))
        .filter(ProcessedMessage.duplicate_of.is_(None))
        .all()
    )
    out = [r for r in rows if (r.receipt_date or "") >= since
           and r.waiting_status not in ("ignored_private_source", "private")]
    out.sort(key=lambda r: r.receipt_date or "", reverse=True)
    return out[:MAX_ROWS]


def read_vat(db: Session, row: ProcessedMessage, drive, analyzer) -> bool:
    mime = "application/pdf"
    name = (row.file_name or "").lower()
    if name.endswith((".jpg", ".jpeg")):
        mime = "image/jpeg"
    elif name.endswith(".png"):
        mime = "image/png"
    data = drive.download_pdf(row.drive_file_id)
    analysis = analyzer.analyze(
        attachment_bytes=data, mime_type=mime, original_filename=row.file_name or "receipt.pdf",
        sender=row.sender or "", subject=row.subject or "", snippet="",
        received_at=row.received_at,
    )
    row.vat_info = analysis.vat_info if analysis.vat_info is not None else {"lines": [],
                                                                            "unread": True}
    db.commit()
    return analysis.vat_info is not None


def backfill(db: Session, *, force: bool = False) -> dict | None:
    from app.services.drive_client import DriveClient
    from app.services.receipt_analyzer import ReceiptAnalyzer

    analyzer = ReceiptAnalyzer()
    if not analyzer.enabled:
        return None
    if not force and not _once(db, TASK):
        return None
    drive = DriveClient()
    done, failed = 0, 0
    for row in candidates(db):
        try:
            if read_vat(db, row, drive, analyzer):
                done += 1
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            failed += 1
            logger.warning("C51: momsen kunde inte läsas för %s: %s", row.message_id, exc)
    logger.info("C51: moms läst från %d kvitton (%d misslyckades)", done, failed)
    return {"read": done, "failed": failed}


def start_in_background() -> None:
    def work():
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            backfill(db)
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("C51: momsläsning misslyckades")
        finally:
            db.close()

    threading.Thread(target=work, name="c51-vat-backfill", daemon=True).start()
