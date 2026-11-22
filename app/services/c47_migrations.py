"""C47 — engångsjobb vid start (idempotenta via maintenance_tasks).

* Del 4: förifyllda prenumerationer (inactive tills Mikko bekräftar).
* Del 5: väntad återbetalning Finnair 134,48 € (bill_line 2513379).
* Del 6: orimliga kvittodatum rättas och flaggas.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import MaintenanceTask

logger = logging.getLogger(__name__)

DATES_TASK = "c47_fix_receipt_dates_v1"


def _once(db: Session, name: str) -> bool:
    if db.query(MaintenanceTask).filter(MaintenanceTask.name == name).first():
        return False
    db.add(MaintenanceTask(name=name))
    return True


def fix_dates_once(db: Session) -> dict | None:
    from app.services.receipt_dates import fix_existing_rows

    if not _once(db, DATES_TASK):
        return None
    out = fix_existing_rows(db)
    logger.info("C47: %d kvittodatum rättade och flaggade: %s", out["fixed"], out["rows"])
    return out


def run_all(db: Session) -> dict:
    from app.services.refunds import seed_finnair_refund
    from app.services.subscriptions import seed_subscriptions
    from app.services.waiting_receipts import adopt_gmail_receipts

    out: dict = {}
    for name, fn in (
        ("subscriptions", lambda: seed_subscriptions(db)),
        ("finnair_refund", lambda: seed_finnair_refund(db)),
        ("dates", lambda: fix_dates_once(db)),
        ("waiting_cutoff", lambda: adopt_gmail_receipts(db)),
    ):
        try:
            out[name] = fn()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("C47: %s misslyckades", name)
    return out
