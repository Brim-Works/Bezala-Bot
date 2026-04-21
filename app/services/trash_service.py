"""Hjälpfunktioner för papperskorgen.

Hanterar sidoeffekterna (Gmail-etikett, Drive-fil) som kringgårdar soft-delete,
restore och hard-delete. Alla Gmail-/Drive-anrop är best-effort: de loggar
eventuella fel men låter DB-operationen gå igenom.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.models import ProcessedMessage

logger = logging.getLogger(__name__)

VALID_REASONS = {"manual", "calendar", "spam", "misclassified"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalise_reason(reason: str | None) -> str:
    if reason and reason in VALID_REASONS:
        return reason
    return "manual"


def soft_delete_row(row: ProcessedMessage, reason: str) -> None:
    row.deleted_at = now_utc()
    row.delete_reason = normalise_reason(reason)


def restore_row(row: ProcessedMessage) -> None:
    row.deleted_at = None
    row.delete_reason = None


def gmail_remove_label_safe(gmail_client, message_id: str) -> None:
    """Best-effort: ta bort Bezala-Klar-etiketten. Loggar men blockerar inte."""
    if not message_id:
        return
    try:
        gmail_client.remove_done(message_id)
    except Exception:  # noqa: BLE001
        logger.exception("Gmail remove_done misslyckades för %s", message_id)


def gmail_mark_done_safe(gmail_client, message_id: str) -> None:
    if not message_id:
        return
    try:
        gmail_client.mark_done(message_id)
    except Exception:  # noqa: BLE001
        logger.exception("Gmail mark_done misslyckades för %s", message_id)


def drive_delete_safe(drive_client, file_id: str) -> bool:
    """Best-effort radering av Drive-fil. Returnerar True vid framgång."""
    if not file_id:
        return False
    try:
        drive_client.delete_file(file_id)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("Drive delete_file misslyckades för %s", file_id)
        return False
