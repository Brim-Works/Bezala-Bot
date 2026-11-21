"""C46 — engångsjobb vid start.

* Del 6: "–"/"—" → "-" i sparade engelska beskrivningar (bara Bezala
  Bots egen databas; Bezala rörs inte).
* Del 5: Anthropic och Bolt/StackBlitz (via Stripe) som avsändare för
  kvitton utan bilaga (html_only_senders).
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import HtmlOnlySender, MaintenanceTask

logger = logging.getLogger(__name__)

SEED_SENDERS_TASK = "seed_html_only_senders_c46"

C46_HTML_ONLY_SENDERS: tuple[tuple[str, str], ...] = (
    ("invoice+statements@mail.anthropic.com", "Anthropic"),
    ("invoice+statements+acct_1epydaahwgq34jln@stripe.com", "Bolt/StackBlitz via Stripe"),
)


def seed_c46_html_only_senders(db: Session) -> int:
    if db.query(MaintenanceTask).filter(MaintenanceTask.name == SEED_SENDERS_TASK).first():
        return 0
    existing = {(s.sender_pattern or "").lower() for s in db.query(HtmlOnlySender).all()}
    added = 0
    for pattern, desc in C46_HTML_ONLY_SENDERS:
        if pattern in existing:
            continue
        db.add(HtmlOnlySender(sender_pattern=pattern, description=desc, is_active=True))
        added += 1
    db.add(MaintenanceTask(name=SEED_SENDERS_TASK))
    db.commit()
    logger.info("C46: html_only_senders — %d nya rader", added)
    return added


DASH_TASK = "c46_plain_dashes_v1"


def fix_saved_descriptions(db: Session) -> int:
    from app.models import ProcessedMessage
    from app.services.text_clean import plain_dashes

    if db.query(MaintenanceTask).filter(MaintenanceTask.name == DASH_TASK).first():
        return 0
    rows = db.query(ProcessedMessage).filter(
        ProcessedMessage.ai_description_en.isnot(None)
    ).all()
    fixed = 0
    for r in rows:
        new = plain_dashes(r.ai_description_en)
        if new != r.ai_description_en:
            r.ai_description_en = new
            fixed += 1
    db.add(MaintenanceTask(name=DASH_TASK))
    db.commit()
    logger.info("C46: %d sparade beskrivningar fick vanligt bindestreck", fixed)
    return fixed
