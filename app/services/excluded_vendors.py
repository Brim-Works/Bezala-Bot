"""FAS 11.1.1 — Exkluderade vendors (SaaS-lista) som aldrig ska
räknas som resekvitton.

Default-listan seedas en gång per deploy via MaintenanceTask. Användaren
kan utöka från UI:t (added_by='user'). Vendor-mönster matchas
case-insensitive som substring mot ProcessedMessage.vendor när
trip_grouper bygger förslag.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import ExcludedVendor, MaintenanceTask

logger = logging.getLogger(__name__)


SEED_TASK_NAME = "seed_excluded_vendors_v1"


# Standardlista över SaaS / återkommande prenumerationer som aldrig
# ska klassas som resekvitton. Hålls liten medvetet — användaren
# utökar listan via Inställningar.
DEFAULT_EXCLUDED_VENDORS: tuple[tuple[str, str], ...] = (
    # AI/Dev
    ("anthropic", "AI/utveckling"),
    ("openai", "AI/utveckling"),
    ("github", "AI/utveckling"),
    ("vercel", "AI/utveckling"),
    ("cursor", "AI/utveckling"),
    # Streaming/Media
    ("spotify", "Streaming"),
    ("netflix", "Streaming"),
    ("youtube", "Streaming"),
    ("disney+", "Streaming"),
    ("hbo", "Streaming"),
    # Cloud/SaaS
    ("aws", "Cloud/SaaS"),
    ("google cloud", "Cloud/SaaS"),
    ("azure", "Cloud/SaaS"),
    ("google workspace", "Cloud/SaaS"),
    ("icloud", "Cloud/SaaS"),
    ("apple.com/bill", "Cloud/SaaS"),
    ("microsoft 365", "Cloud/SaaS"),
    # Productivity
    ("notion", "Productivity"),
    ("slack", "Productivity"),
    ("zoom", "Productivity"),
    ("atlassian", "Productivity"),
    ("linear", "Productivity"),
    ("figma", "Productivity"),
    ("canva", "Productivity"),
    ("1password", "Productivity"),
    ("lastpass", "Productivity"),
    # Telekom (svenska + finska)
    ("telia", "Telekom"),
    ("telenor", "Telekom"),
    ("elisa", "Telekom"),
    ("dna", "Telekom"),
    # Övrigt
    ("adobe", "Övrigt"),
    ("dropbox", "Övrigt"),
    ("evernote", "Övrigt"),
)


def list_excluded_vendor_patterns(db: Session) -> list[str]:
    """Plocka alla aktiva mönster (lowercased) från DB. Säker att kalla
    även om tabellen saknas (fångas av kallaren)."""
    return [
        v.vendor_pattern.lower()
        for v in db.query(ExcludedVendor).all()
    ]


def is_vendor_excluded(
    vendor: str | None, patterns: Iterable[str],
) -> bool:
    """True om vendor matchar något av mönstren (case-insensitive
    substring). Tomt vendor → False."""
    if not vendor:
        return False
    needle = vendor.lower()
    for p in patterns:
        if p and p in needle:
            return True
    return False


def seed_default_vendors(db: Session) -> int:
    """Idempotent — körs en gång per deploy via MaintenanceTask.
    Lägger till ev. nya patterns från DEFAULT_EXCLUDED_VENDORS som inte
    finns i DB ännu (added_by='system'). Returnerar antal nya rader."""
    existing_task = (
        db.query(MaintenanceTask)
        .filter(MaintenanceTask.name == SEED_TASK_NAME)
        .first()
    )
    if existing_task is not None:
        return 0

    existing_patterns = {
        v.vendor_pattern.lower()
        for v in db.query(ExcludedVendor).all()
    }
    added = 0
    for pattern, description in DEFAULT_EXCLUDED_VENDORS:
        norm = pattern.lower().strip()
        if not norm or norm in existing_patterns:
            continue
        db.add(ExcludedVendor(
            vendor_pattern=norm,
            description=description,
            added_by="system",
        ))
        added += 1
    db.add(MaintenanceTask(name=SEED_TASK_NAME))
    db.commit()
    logger.info("excluded_vendors seedad — %d nya rader", added)
    return added


def add_user_vendor(
    db: Session, pattern: str, description: str | None = None,
) -> tuple[ExcludedVendor, bool]:
    """Lägg till en user-vendor. Idempotent — om mönstret redan finns
    returneras existerande raden + already_exists=True."""
    norm = (pattern or "").strip().lower()
    if not norm:
        raise ValueError("pattern saknas")
    existing = (
        db.query(ExcludedVendor)
        .filter(ExcludedVendor.vendor_pattern == norm)
        .first()
    )
    if existing is not None:
        return existing, True
    row = ExcludedVendor(
        vendor_pattern=norm,
        description=(description or "").strip() or None,
        added_by="user",
    )
    db.add(row)
    db.commit()
    return row, False


def remove_vendor(db: Session, vendor_id: int) -> bool:
    row = db.query(ExcludedVendor).filter_by(id=vendor_id).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
