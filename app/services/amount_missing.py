"""C48 Del 2 — kvitton utan belopp.

Orsak (Arlanda Express 3 nov, kvitto 732): mailet har bara BILJETTEN som
PDF-bilaga (utan pris). Själva kvittot ligger bakom länken "Download
receipt pdf". Länk-hämtaren för Arlanda (vendor_handlers) hittade aldrig
länken, eftersom det nya mailet skriver sluttaggen som "</a\n>" och
länk-sökarens regex krävde "</a>". AI:n läste alltså biljetten och satte
korrekt amount=null. Regexen är rättad (link_extractor).

Här:
* `rows()`: alla bearbetade 2026-kvitton med amount = null.
* `report_and_reread()`: listar dem i cleanup_log (regel amount_missing)
  och läser om de som går (jobb-Gmail, inte kopplade i Bezala) med
  pipelinen i force-läge UTAN auto-upload (inga Bezala-utkast).
* Ett kvitto utan belopp får aldrig auto-kopplas (waiting_receipts kräver
  beloppspoäng; prenumerationer hoppar över det) och svep-kortet visar
  "Amount missing - open the receipt before coupling".
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy.orm import Session

from app.models import MaintenanceTask, ProcessedMessage

logger = logging.getLogger(__name__)

TASK = "c48_reread_amount_missing_v1"
RUN = "c48_amount_missing_v1"
_COPY_BACK = ("waiting_status", "waiting_since", "review_flag", "payment_method")


def rows(db: Session) -> list[ProcessedMessage]:
    from app.services.trip_completeness import _parse_day

    out = []
    for m in (db.query(ProcessedMessage)
              .filter(ProcessedMessage.status == "saved")
              .filter(ProcessedMessage.deleted_at.is_(None))
              .filter(ProcessedMessage.duplicate_of.is_(None))
              .filter(ProcessedMessage.amount.is_(None)).all()):
        d = _parse_day(m.receipt_date) or _parse_day(m.received_at)
        if d is not None and d.year == 2026:
            out.append(m)
    return sorted(out, key=lambda m: (m.receipt_date or "", m.id))


def can_reread(m: ProcessedMessage) -> str | None:
    """None = kan läsas om, annars orsak."""
    if m.bezala_transaction_id:
        return "coupled in Bezala - not re-read"
    if (m.upload_source or "gmail") != "gmail":
        return f"source {m.upload_source} - no mail to re-read"
    if (m.source_account or "visma") != "visma":
        return "private Gmail - re-read not supported"
    if str(m.message_id).startswith(("manual:", "mobile:", "mail_kvitto:")) or "#" in str(m.message_id):
        return "not a Gmail message id"
    return None


def brief(m: ProcessedMessage) -> dict:
    return {"id": m.id, "message_id": m.message_id, "vendor": m.vendor,
            "date": m.receipt_date, "file": m.file_name, "source": m.upload_source,
            "account": m.source_account, "currency": m.currency}


def _snapshot(m: ProcessedMessage) -> dict:
    return {c.name: getattr(m, c.name) for c in ProcessedMessage.__table__.columns
            if c.name != "id"}


def reread(message_ids: list[str], *, force_process=None, session_factory=None) -> list[dict]:
    """Läs om utan auto-upload. Misslyckas omläsningen återställs raden."""
    from app.db import session_scope as _scope

    scope = session_factory or _scope
    if force_process is None:
        from app.services.pipeline import force_process_message_ids as force_process
    snaps: dict[str, dict] = {}
    with scope() as db:
        for m in db.query(ProcessedMessage).filter(
                ProcessedMessage.message_id.in_(message_ids)).all():
            snaps[m.message_id] = _snapshot(m)
    try:
        result = force_process(list(snaps), auto_upload=False)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[c48-amount] omläsning misslyckades")
        result = {"details": [], "error": str(exc)}
    out = []
    from app.services.cleanup_log import log

    with scope() as db:
        for mid, snap in snaps.items():
            row = (db.query(ProcessedMessage)
                   .filter(ProcessedMessage.message_id == mid).first())
            if row is None or row.status != "saved":
                if row is not None:
                    db.delete(row)
                    db.flush()
                db.add(ProcessedMessage(**snap))
                outcome = {"restored": True, "amount": None}
            else:
                for k in _COPY_BACK:
                    if getattr(row, k) is None and snap.get(k) is not None:
                        setattr(row, k, snap[k])
                outcome = {"amount": row.amount, "currency": row.currency,
                           "vendor": row.vendor, "file": row.file_name}
            log(db, RUN, "amount_missing", mid,
                before={"amount": None, "vendor": snap.get("vendor"),
                        "file": snap.get("file_name")},
                after=outcome,
                reason=("re-read - amount found" if outcome.get("amount") is not None
                        else "re-read - amount still missing"))
            out.append({"message_id": mid, **outcome})
    logger.info("[c48-amount] omläsning: %s (%s)", out, result.get("error"))
    return out


def report_and_reread(db: Session, *, background: bool = True, runner=None) -> dict | None:
    """Engångs: lista alla och starta omläsning i bakgrunden."""
    from app.services.cleanup_log import log

    if db.query(MaintenanceTask).filter(MaintenanceTask.name == TASK).first():
        return None
    found = rows(db)
    if not background:  # skanning avstängd: bara räkna, gör om vid nästa start
        return {"without_amount": len(found), "reread": [], "note": "scan disabled"}
    todo = []
    for m in found:
        why = can_reread(m)
        if why is None:
            todo.append(m.message_id)
        log(db, RUN, "amount_missing", m.message_id, before=brief(m),
            reason=why or "will be re-read", dry_run=True)
    db.add(MaintenanceTask(name=TASK))
    db.commit()
    logger.info("[c48-amount] %d kvitton 2026 utan belopp, %d läses om", len(found), len(todo))
    if todo:
        if runner is not None:
            runner(todo)
        else:
            threading.Thread(target=reread, args=(todo,), name="c48-reread",
                             daemon=True).start()
    return {"without_amount": len(found), "reread": todo}


def list_without_amount(db: Session) -> dict:
    return {
        "receipts": [{**brief(m), "reread": can_reread(m) or "yes"} for m in rows(db)],
    }
