"""C47 Del 6.6 — datastäd (engångs vid start, idempotent).

* Tomma felrader ("—" överallt): status 'error' utan avsändare, ämne och
  fil. Orsak: pipeline._log_error anropas utan mail-kontext när själva
  hämtningen av mailet misslyckas (t.ex. mailet raderat i Gmail eller
  privat avsändare utanför listan) — raden får då bara message_id + fel.
  Orsakerna loggas (grupperade) och raderna rensas; mailen bedöms igen
  vid nästa skanning om de fortfarande finns.
* Kvitton daterade före 2026-01-01 från C46-omläsningen → papperskorgen
  (orsak 'pre_2026'). Kopplade (Bezala) kvitton rörs inte.
* Dubbletter (C44-regeln + C47:s Finnair/hotell-regler) markeras.
"""

from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy.orm import Session

from app.models import MaintenanceTask, ProcessedMessage

logger = logging.getLogger(__name__)

TASK = "c47_cleanup_v1"
RESCAN_TASK = "c46_rescan_work_2026_v1"


def empty_error_rows(db: Session) -> list[ProcessedMessage]:
    return [
        m for m in db.query(ProcessedMessage)
        .filter(ProcessedMessage.status == "error")
        .filter(ProcessedMessage.deleted_at.is_(None)).all()
        if not (m.sender or m.subject or m.file_name or m.drive_file_id or m.vendor)
    ]


def clean_empty_error_rows(db: Session) -> dict:
    rows = empty_error_rows(db)
    causes = Counter((m.error_message or "(inget felmeddelande)")[:80] for m in rows)
    for m in rows:
        db.delete(m)
    db.commit()
    logger.info("[c47-cleanup] %d tomma felrader rensade, orsaker: %s",
                len(rows), causes.most_common(10))
    return {"deleted": len(rows), "causes": causes.most_common(10)}


def trash_pre_2026(db: Session) -> dict:
    from app.services.trash_service import soft_delete_row

    task = db.query(MaintenanceTask).filter(MaintenanceTask.name == RESCAN_TASK).first()
    if task is None:
        return {"trashed": 0, "note": "C46-omläsningen har inte körts"}
    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(ProcessedMessage.receipt_date.isnot(None))
        .filter(ProcessedMessage.receipt_date < "2026-01-01")
        .filter(ProcessedMessage.bezala_transaction_id.is_(None))
        .filter(ProcessedMessage.processed_at >= task.ran_at)
        .all()
    )
    out = []
    for m in rows:
        soft_delete_row(m, "pre_2026")
        out.append(f"{m.vendor} {m.receipt_date}")
    db.commit()
    logger.info("[c47-cleanup] %d kvitton före 2026 till papperskorgen: %s", len(out), out[:30])
    return {"trashed": len(out), "rows": out[:30]}


def mark_all_duplicates(db: Session) -> dict:
    from app.services.receipt_details import mark_duplicates

    out = mark_duplicates(db)
    logger.info("[c47-cleanup] dubbletter: %s", out)
    return out


def run_once(db: Session) -> dict | None:
    if db.query(MaintenanceTask).filter(MaintenanceTask.name == TASK).first():
        return None
    out = {}
    for name, fn in (("empty_errors", clean_empty_error_rows), ("pre_2026", trash_pre_2026),
                     ("duplicates", mark_all_duplicates)):
        try:
            out[name] = fn(db)
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("[c47-cleanup] %s misslyckades", name)
    db.add(MaintenanceTask(name=TASK))
    db.commit()
    return out
