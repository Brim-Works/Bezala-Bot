"""APScheduler-setup. Intervall läses från AppSettings i DB."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.db import session_scope
from app.services.pipeline import run_scan
from app.services.settings_service import load_settings
from app.services.trash_scheduler import purge_old_trash

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_JOB_ID = "gmail_scan"
_TRASH_JOB_ID = "trash_purge"
_TRIP_JOB_ID = "trip_analysis"


def _trip_analysis_job() -> None:
    """FAS 11.1 — generera resa-förslag varje natt kl 04:00 UTC.

    Idempotent: persist_suggestions skipar förslag som överlappar med
    redan existerande resor."""
    logger.info("Trip-analys startar.")
    try:
        from app.services.trip_grouper import (
            persist_suggestions, suggest_trips,
        )
        with session_scope() as db:
            suggestions = suggest_trips(db, lookback_days=90)
            saved = persist_suggestions(db, suggestions)
            logger.info(
                "Trip-analys klar — %d nya förslag (kandidater=%d)",
                len(saved), len(suggestions),
            )
    except Exception:
        logger.exception("Trip-analys kraschade.")


def _scan_job() -> None:
    logger.info("Schemalagd scanning startar.")
    try:
        result = run_scan()
        logger.info(
            "Schemalagd scanning klar: hittade=%d processade=%d hoppade=%d fel=%d",
            result.found,
            result.processed,
            result.skipped,
            result.errors,
        )
    except Exception:
        logger.exception("Schemalagd scanning kraschade.")


def _read_interval_from_db(fallback: int) -> int:
    try:
        with session_scope() as db:
            row = load_settings(db)
            return row.scan_interval_minutes
    except Exception:
        logger.exception("Kunde inte läsa scan-intervall från DB, använder fallback.")
        return fallback


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    env = get_settings()
    if not env.scan_enabled:
        logger.info("SCAN_ENABLED=false — scheduler startas inte.")
        return None
    if _scheduler and _scheduler.running:
        return _scheduler

    interval = _read_interval_from_db(env.scan_interval_minutes)

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _scan_job,
        trigger=IntervalTrigger(minutes=interval),
        id=_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        purge_old_trash,
        trigger=CronTrigger(hour=3, minute=0, timezone="UTC"),
        id=_TRASH_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.add_job(
        _trip_analysis_job,
        trigger=CronTrigger(hour=4, minute=0, timezone="UTC"),
        id=_TRIP_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler startad — scanning var %d:e minut, "
        "trash-purge 03:00 UTC, trip-analys 04:00 UTC.",
        interval,
    )
    return _scheduler


def reschedule_scheduler(interval_minutes: int) -> None:
    """Uppdatera scanning-intervallet på ett körande scheduler."""
    if not _scheduler or not _scheduler.running:
        logger.info("Scheduler körs inte — reschedule hoppas över.")
        return
    _scheduler.reschedule_job(
        _JOB_ID,
        trigger=IntervalTrigger(minutes=interval_minutes),
    )
    logger.info("Scheduler omkonfigurerad — scanning var %d:e minut.", interval_minutes)


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler avstängd.")
        _scheduler = None
