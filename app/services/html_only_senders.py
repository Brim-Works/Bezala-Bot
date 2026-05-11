"""HTML-only senders — avsändare som skickar kvittot i mail-bodyn
istället för som PDF-bilaga (Skånetrafiken, Moovy notifieringar,
Cursor, Airport LRS m.fl.).

För dessa avsändare hoppar Gmail-queryn `has:attachment`-filtret och
mailen processas via html_to_pdf-pipelinen (pipeline._process_one_message
~rad 488).

Default-listan seedas en gång per deploy via MaintenanceTask. Användaren
utökar via Inställningar.
"""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import HtmlOnlySender, MaintenanceTask

logger = logging.getLogger(__name__)


SEED_TASK_NAME = "seed_html_only_senders_v1"


# Default-listan baserad på Match Health-rapporten:
# - SKANETRAFIKEN: 2 mail finns, 0 med attachment
# - MOOVY (kortdebitering-notiser): 2 mail finns, 0 med attachment
# - CURSOR: 1 mail finns, 0 med attachment
# - AIRPORT LRS (Stockholm Arlanda P-bolaget): 1 mail, 0 med attachment
DEFAULT_HTML_ONLY_SENDERS: tuple[tuple[str, str], ...] = (
    ("skanetrafiken", "Skånetrafiken — tågbiljetter Skåne"),
    ("noreply@moovy.fi", "Moovy parkering — kortdebitering-notiser"),
    ("cursor", "Cursor AI — månadsfaktura"),
    ("airport", "Airport LRS — parkering Arlanda"),
)


def list_html_only_senders(
    db: Session, *, only_active: bool = False,
) -> list[HtmlOnlySender]:
    """Returnera alla html_only_senders sorterade på id."""
    q = db.query(HtmlOnlySender)
    if only_active:
        q = q.filter(HtmlOnlySender.is_active.is_(True))
    return q.order_by(HtmlOnlySender.id.asc()).all()


def list_active_patterns(db: Session) -> list[str]:
    """Returnera lowercased patterns för Gmail-query-byggaren.
    Inaktiva senders skippas."""
    return [
        (s.sender_pattern or "").lower()
        for s in list_html_only_senders(db, only_active=True)
        if (s.sender_pattern or "").strip()
    ]


def is_html_only_sender(
    sender: str | None, patterns: Iterable[str],
) -> bool:
    """True om sender matchar något pattern (case-insensitive substring).
    Tom sender → False."""
    if not sender:
        return False
    needle = sender.lower()
    for p in patterns:
        if p and p in needle:
            return True
    return False


def seed_default_html_only_senders(db: Session) -> int:
    """Idempotent — körs en gång per deploy via MaintenanceTask.
    Returnerar antal nya rader."""
    existing_task = (
        db.query(MaintenanceTask)
        .filter(MaintenanceTask.name == SEED_TASK_NAME)
        .first()
    )
    if existing_task is not None:
        return 0

    existing_patterns = {
        (s.sender_pattern or "").lower()
        for s in db.query(HtmlOnlySender).all()
    }
    added = 0
    for pattern, description in DEFAULT_HTML_ONLY_SENDERS:
        norm = pattern.lower().strip()
        if not norm or norm in existing_patterns:
            continue
        db.add(HtmlOnlySender(
            sender_pattern=norm,
            description=description,
            is_active=True,
        ))
        added += 1
    db.add(MaintenanceTask(name=SEED_TASK_NAME))
    db.commit()
    logger.info("html_only_senders seedad — %d nya rader", added)
    return added


def add_sender(
    db: Session, pattern: str, description: str | None = None,
) -> tuple[HtmlOnlySender, bool]:
    """Lägg till en sender. Idempotent — om mönstret redan finns
    returneras existerande raden + already_exists=True."""
    norm = (pattern or "").strip().lower()
    if not norm:
        raise ValueError("pattern saknas")
    existing = (
        db.query(HtmlOnlySender)
        .filter(HtmlOnlySender.sender_pattern == norm)
        .first()
    )
    if existing is not None:
        return existing, True
    row = HtmlOnlySender(
        sender_pattern=norm,
        description=(description or "").strip() or None,
        is_active=True,
    )
    db.add(row)
    db.commit()
    return row, False


def remove_sender(db: Session, sender_id: int) -> bool:
    row = db.query(HtmlOnlySender).filter_by(id=sender_id).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def set_active(
    db: Session, sender_id: int, is_active: bool,
) -> HtmlOnlySender | None:
    """Toggle active-flaggan. Returnerar uppdaterad rad eller None om
    den inte finns."""
    row = db.query(HtmlOnlySender).filter_by(id=sender_id).first()
    if row is None:
        return None
    row.is_active = bool(is_active)
    db.commit()
    db.refresh(row)
    return row


def serialize(row: HtmlOnlySender) -> dict:
    return {
        "id": row.id,
        "sender_pattern": row.sender_pattern,
        "description": row.description,
        "is_active": bool(row.is_active),
        "created_at": (
            row.created_at.isoformat()
            if row.created_at is not None else None
        ),
    }
