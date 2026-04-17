"""APScheduler-setup. Kör scanning varje timme."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.services.pipeline import run_scan

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


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


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    settings = get_settings()
    if not settings.scan_enabled:
        logger.info("SCAN_ENABLED=false — scheduler startas inte.")
        return None
    if _scheduler and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _scan_job,
        trigger=IntervalTrigger(minutes=settings.scan_interval_minutes),
        id="gmail_scan",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler startad — scanning var %d:e minut.",
        settings.scan_interval_minutes,
    )
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler avstängd.")
        _scheduler = None
