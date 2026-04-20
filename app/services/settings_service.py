"""Inställningar som singleton-rad i DB.

load_settings returnerar AppSettings-raden (id=1) och skapar den med defaults
om den inte finns. build_gmail_query bygger en Gmail-sökquery från
inställningsraden, inklusive include/exclude av avsändare och ämnen.
"""

from __future__ import annotations

from typing import Iterable

from app.config import get_settings as get_env_settings
from app.models import AppSettings


SETTINGS_ID = 1


def load_settings(db) -> AppSettings:
    """Hämta settings-raden, skapa med env-baserade defaults om den saknas."""
    row = db.query(AppSettings).filter(AppSettings.id == SETTINGS_ID).first()
    if row is not None:
        return row

    env = get_env_settings()
    row = AppSettings(
        id=SETTINGS_ID,
        scan_interval_minutes=env.scan_interval_minutes,
        ai_naming_enabled=True,
        auto_upload_enabled=False,
        confidence_threshold=90,
        require_attachments=True,
        exclude_promotions=True,
        exclude_social=True,
        exclude_calendar=True,
        include_senders=[],
        exclude_senders=[],
        exclude_subjects=[],
    )
    db.add(row)
    db.flush()
    return row


def settings_to_dict(row: AppSettings) -> dict:
    return {
        "scan_interval_minutes": row.scan_interval_minutes,
        "ai_naming_enabled": row.ai_naming_enabled,
        "auto_upload_enabled": row.auto_upload_enabled,
        "confidence_threshold": row.confidence_threshold,
        "require_attachments": row.require_attachments,
        "exclude_promotions": row.exclude_promotions,
        "exclude_social": row.exclude_social,
        "exclude_calendar": row.exclude_calendar,
        "include_senders": list(row.include_senders or []),
        "exclude_senders": list(row.exclude_senders or []),
        "exclude_subjects": list(row.exclude_subjects or []),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _clean_list(items: Iterable[str] | None) -> list[str]:
    return [s.strip() for s in (items or []) if s and s.strip()]


def build_gmail_query(row: AppSettings, done_label: str) -> str:
    """Bygg Gmail-query från inställningarna."""
    parts: list[str] = [
        f'-label:"{done_label}"',
        "-in:spam",
        "-in:trash",
    ]

    if row.require_attachments:
        parts.append("has:attachment")
    if row.exclude_promotions:
        parts.append("-category:promotions")
    if row.exclude_social:
        parts.append("-category:social")
    if row.exclude_calendar:
        parts.append("-filename:ics")

    for sender in _clean_list(row.exclude_senders):
        parts.append(f"-from:{sender}")

    for subj in _clean_list(row.exclude_subjects):
        safe = subj.replace('"', '\\"')
        parts.append(f'-subject:"{safe}"')

    include = _clean_list(row.include_senders)
    if include:
        or_clause = " OR ".join(f"from:{s}" for s in include)
        parts.append(f"({or_clause})")

    return " ".join(parts)


def subject_matches_exclusion(subject: str | None, excluded: Iterable[str]) -> bool:
    """Client-side dubbelkoll — returnerar True om subject innehåller någon
    av de exkluderade fraserna (case-insensitive)."""
    if not subject:
        return False
    lower = subject.lower()
    return any(term.lower() in lower for term in _clean_list(excluded))
