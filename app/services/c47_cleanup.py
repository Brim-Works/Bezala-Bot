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


def clean_empty_error_rows(db: Session, run: str = TASK) -> dict:
    from app.services.cleanup_log import log

    rows = empty_error_rows(db)
    causes = Counter((m.error_message or "(inget felmeddelande)")[:80] for m in rows)
    for m in rows:
        log(db, run, "empty_error", m.message_id, before=_err_row(m),
            after={"deleted": True}, reason=m.error_message or "(no error message)")
        db.delete(m)
    db.commit()
    logger.info("[c47-cleanup] %d tomma felrader rensade, orsaker: %s",
                len(rows), causes.most_common(10))
    return {"deleted": len(rows), "causes": causes.most_common(10)}


def _err_row(m) -> dict:
    return {"id": m.id, "status": m.status, "account": m.source_account,
            "processed_at": m.processed_at, "error": (m.error_message or "")[:300]}


def trash_pre_2026(db: Session, run: str = TASK) -> dict:
    from app.services.cleanup_log import log
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
        log(db, run, "pre_2026", m.message_id,
            before={"vendor": m.vendor, "receipt_date": m.receipt_date, "file": m.file_name},
            after={"trashed": True}, reason="receipt dated before 2026 from the 2026 re-read")
    db.commit()
    logger.info("[c47-cleanup] %d kvitton före 2026 till papperskorgen: %s", len(out), out[:30])
    return {"trashed": len(out), "rows": out[:30]}


def mark_all_duplicates(db: Session, run: str = "duplicates") -> dict:
    from app.services.receipt_details import mark_duplicates

    marked: list[dict] = []
    out = mark_duplicates(db, marked)
    if marked:
        _log_duplicates(db, run, marked)
    logger.info("[c47-cleanup] dubbletter: %s", out)
    return out


def _duplicate_rule(db: Session, ids: set[int]) -> dict[int, str]:
    """Vilken regel gav dubbletten (finnair/hotell/övrigt)."""
    from app.services.c47_duplicates import finnair_pairs, hotel_pairs

    rows = (db.query(ProcessedMessage)
            .filter(ProcessedMessage.deleted_at.is_(None))
            .filter(ProcessedMessage.status == "saved").all())
    out: dict[int, str] = {}
    for _keep, dupe in finnair_pairs(rows):
        if dupe.id in ids:
            out[dupe.id] = "finnair_duplicate"
    for _keep, dupe in hotel_pairs(rows):
        if dupe.id in ids:
            out.setdefault(dupe.id, "hotel_duplicate")
    return out


def _log_duplicates(db: Session, run: str, marked: list[dict], dry_run: bool = False) -> None:
    from app.services.cleanup_log import log

    rules = _duplicate_rule(db, {r["id"] for r in marked})
    for r in marked:
        log(db, run, rules.get(r["id"], "duplicate"), r["message_id"],
            before={"duplicate_of": r.get("before"), "vendor": r.get("vendor")},
            after={"duplicate_of": r["duplicate_of"]},
            reason=f"duplicate of {r['duplicate_of']}", dry_run=dry_run)
    db.commit()


BACKFILL_RUN = "c48_backfill_v1"


def backfill_cleanup_log(db: Session) -> dict | None:
    """C48 Del 6: skriv bakåt vad C47-städningen gjort (från databasens
    nuvarande tillstånd) + torrkörning av vad den skulle göra nu.
    Engångs (run=c48_backfill_v1)."""
    from app.services.cleanup_log import already_logged, log
    from app.services.receipt_dates import FLAG_DATE_CORRECTED

    if already_logged(db, BACKFILL_RUN):
        return None
    out: dict = {}
    # 1. Tomma felrader som finns kvar (C47 raderade sina; torrläge nu).
    rows = empty_error_rows(db)
    for m in rows:
        log(db, BACKFILL_RUN, "empty_error", m.message_id, before=_err_row(m),
            after={"would_delete": True}, reason=m.error_message or "(no error message)",
            dry_run=True)
    out["empty_error_now"] = len(rows)
    # 2. Före 2026 → papperskorgen (gjort av C47).
    trashed = (db.query(ProcessedMessage)
               .filter(ProcessedMessage.delete_reason == "pre_2026").all())
    for m in trashed:
        log(db, BACKFILL_RUN, "pre_2026", m.message_id,
            before={"vendor": m.vendor, "receipt_date": m.receipt_date, "file": m.file_name},
            after={"trashed": True, "deleted_at": m.deleted_at},
            reason="receipt dated before 2026 from the 2026 re-read")
    out["pre_2026"] = len(trashed)
    # 3. Datumrättningar (gamla datumet sparades inte av C47).
    fixed = (db.query(ProcessedMessage)
             .filter(ProcessedMessage.review_flag == FLAG_DATE_CORRECTED).all())
    for m in fixed:
        log(db, BACKFILL_RUN, "date_corrected", m.message_id,
            before={"receipt_date": "(not stored by C47)", "received_at": m.received_at},
            after={"receipt_date": m.receipt_date, "flag": m.review_flag},
            reason=f"{m.vendor or '?'} - {m.file_name or ''}".strip(" -"))
    out["date_corrected"] = len(fixed)
    # 4. Dubbletter (alla som är markerade nu).
    dups = (db.query(ProcessedMessage)
            .filter(ProcessedMessage.duplicate_of.isnot(None))
            .filter(ProcessedMessage.deleted_at.is_(None)).all())
    _log_duplicates(db, BACKFILL_RUN, [
        {"id": m.id, "message_id": m.message_id, "vendor": m.vendor,
         "before": None, "duplicate_of": m.duplicate_of} for m in dups])
    out["duplicates"] = len(dups)
    if not any(out.values()):
        log(db, BACKFILL_RUN, "backfill", None, reason="nothing to backfill")
    db.commit()
    logger.info("[c48-cleanup] cleanup_log bakåt: %s", out)
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
