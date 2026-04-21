"""Trash auto-purge — daglig scheduler-job.

Läser `app_settings.trash_auto_purge_days`. När värdet är 0 (default) görs
inget. Annars hard-deletas rader där `deleted_at` är äldre än N dagar.
Drive-filer behålls alltid av auto-purge (samma policy som manuell
hard-delete där purge_drive=false är default).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.db import session_scope
from app.models import ProcessedMessage
from app.services.settings_service import load_settings

logger = logging.getLogger(__name__)


def purge_old_trash() -> int:
    """Kör ett purge-pass. Returnerar antalet raderade rader (0 om av)."""
    try:
        with session_scope() as db:
            row = load_settings(db)
            days = int(row.trash_auto_purge_days or 0)
            if days <= 0:
                logger.debug("Trash auto-purge är av (days=%d).", days)
                return 0
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            deleted = (
                db.query(ProcessedMessage)
                .filter(ProcessedMessage.deleted_at.is_not(None))
                .filter(ProcessedMessage.deleted_at < cutoff)
                .delete(synchronize_session=False)
            )
            if deleted:
                logger.info(
                    "Trash auto-purge: tog bort %d rader äldre än %d dagar.",
                    deleted,
                    days,
                )
            return int(deleted or 0)
    except Exception:
        logger.exception("Trash auto-purge misslyckades.")
        return 0
