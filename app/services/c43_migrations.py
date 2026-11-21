"""C43 — engångsmigreringar vid startup (idempotenta via MaintenanceTask).

1. Nya html-only-avsändare för leverantörer vars kvitto bara finns i
   mailtexten (MJ'S hotell via bookvisit.com).
2. Backfill av `kind = 'change_fee'` på redan sparade Finnair/Amadeus-rader
   (ombokningar med prisskillnad på några cent). Okopplade rader får även
   den engelska Bezala-beskrivningen omskriven. Varje träff loggas med
   prefixet `[c43-change-fee]` så historiska fall syns i Railway-loggen.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import HtmlOnlySender, MaintenanceTask, ProcessedMessage
from app.services.receipt_kind import (
    KIND_CHANGE_FEE,
    classify_existing_row,
    detect_change_fee,
)

logger = logging.getLogger(__name__)

SEED_SENDERS_TASK = "seed_html_only_senders_c43"
BACKFILL_CHANGE_FEE_TASK = "c43_backfill_change_fee_v1"

C43_HTML_ONLY_SENDERS: tuple[tuple[str, str], ...] = (
    # MJ'S hotell (Malmö) — bokningsbekräftelsen från bookvisit är kvittot
    # (Klarna-betalning, moms specificerad). Kortraden heter "MJS.LIFE".
    ("noreply@bookvisit.com", "Bookvisit (MJ'S m.fl. hotell) — bokningsbekräftelse = kvitto"),
)


def _task_done(db: Session, name: str) -> bool:
    return (
        db.query(MaintenanceTask).filter(MaintenanceTask.name == name).first()
        is not None
    )


def seed_c43_html_only_senders(db: Session) -> int:
    if _task_done(db, SEED_SENDERS_TASK):
        return 0
    existing = {
        (s.sender_pattern or "").lower() for s in db.query(HtmlOnlySender).all()
    }
    added = 0
    for pattern, description in C43_HTML_ONLY_SENDERS:
        if pattern in existing:
            continue
        db.add(HtmlOnlySender(
            sender_pattern=pattern, description=description, is_active=True,
        ))
        added += 1
    db.add(MaintenanceTask(name=SEED_SENDERS_TASK))
    db.commit()
    logger.info("C43: html_only_senders — %d nya rader", added)
    return added


def backfill_change_fee_kind(db: Session) -> list[int]:
    """Märk historiska Finnair/Amadeus-ombokningar. Returnerar id:n."""
    if _task_done(db, BACKFILL_CHANGE_FEE_TASK):
        return []
    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(ProcessedMessage.kind.is_(None))
        .all()
    )
    hits: list[int] = []
    for r in rows:
        kind = classify_existing_row(
            sender=r.sender, subject=r.subject, summary=r.summary,
            amount=r.amount,
        )
        if kind != KIND_CHANGE_FEE:
            continue
        r.kind = KIND_CHANGE_FEE
        hits.append(r.id)
        coupled = bool(r.bezala_transaction_id) or r.bezala_upload_status == "success"
        if not coupled:
            info = detect_change_fee(
                sender=r.sender, subject=r.subject,
                text=f"{r.summary or ''}\neticket", amount=r.amount,
                currency=r.currency,
            )
            if info is not None:
                r.ai_description_en = info.description_en()
        logger.info(
            "[c43-change-fee] id=%s received=%s sender=%r subject=%r "
            "amount=%s %s coupled=%s",
            r.id, r.received_at, r.sender, r.subject, r.amount, r.currency,
            coupled,
        )
    db.add(MaintenanceTask(name=BACKFILL_CHANGE_FEE_TASK))
    db.commit()
    logger.info("C43: change_fee-backfill — %d rader märkta: %s", len(hits), hits)
    return hits


def run_c43_migrations(db: Session) -> None:
    try:
        seed_c43_html_only_senders(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("C43: seed html_only_senders misslyckades")
    try:
        backfill_change_fee_kind(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("C43: change_fee-backfill misslyckades")
