"""C47 — körs efter varje Bezala-synk av kortrader (schemalagt var 2:a
timme). Ordning: återbetalningar → prenumerationer → väntande kvitton →
nya prenumerationsförslag. Varje steg är isolerat: ett fel loggas och
nästa steg körs ändå."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def after_card_sync(db: Session, *, couple=None) -> dict:
    from app.services.refunds import match_refunds
    from app.services.subscriptions import detect_subscriptions, process_lines
    from app.services.waiting_receipts import match_waiting_receipts

    steps = (
        ("refunds", lambda: match_refunds(db)),
        ("subscriptions", lambda: process_lines(db, couple=couple)),
        ("waiting_receipts", lambda: match_waiting_receipts(db, couple=couple)),
        ("new_subscriptions", lambda: [s.name for s in detect_subscriptions(db)]),
    )
    out: dict = {}
    for name, fn in steps:
        try:
            out[name] = fn()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception("[c47-sync] %s misslyckades", name)
            out[name] = {"error": str(exc)[:300]}
    logger.info("[c47-sync] klart: %s", out)
    return out
