"""APScheduler integration for smart-search Sync Service.

Provides build_scheduler() which returns a configured BackgroundScheduler
(or None when schedule is 'manual' / not a cron expression).

Cron detection: a valid cron string has exactly 5 whitespace-separated fields,
each being digits, '*', '/', '-', or ','. Anything else (including 'manual',
empty string) is treated as disabled.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_CRON_FIELD_RE = re.compile(r'^[\d*/,\-]+$')


def is_cron_schedule(schedule: str) -> bool:
    """Return True iff *schedule* looks like a 5-field cron expression."""
    if not schedule or schedule.strip().lower() == "manual":
        return False
    parts = schedule.strip().split()
    if len(parts) != 5:
        return False
    return all(_CRON_FIELD_RE.match(p) for p in parts)


def build_scheduler(app_state, settings):
    """Create and return a started BackgroundScheduler, or None if disabled.

    The scheduler job:
      1. Tries sync_lock.acquire(blocking=False).
      2. If acquired  → calls _run_sync_bg(app_state, mode='incremental',
                         triggered_by='scheduler').
         _run_sync_bg owns the lock and releases it in its finally block.
      3. If not acquired → logs a warning; writes a skipped log entry via
                           app_state.log_store (if present).
    """
    schedule = settings.sync.schedule
    if not is_cron_schedule(schedule):
        logger.info("Scheduler disabled (schedule=%r). Set a cron expression to enable.", schedule)
        return None

    # Lazy imports — only reached when a valid cron schedule is configured.
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from api.sync import _run_sync_bg

    minute, hour, day, month, day_of_week = schedule.strip().split()

    def _scheduled_job():
        if not app_state.sync_lock.acquire(blocking=False):
            logger.warning(
                "Scheduler fired but sync already running — skipping. "
                "Recording skipped entry."
            )
            # log_store may not exist yet in plan-01 (wired in plan-02);
            # guard with getattr so plan-01 tests pass without log_store.
            log_store = getattr(app_state, "log_store", None)
            if log_store is not None:
                import datetime as _dt
                now = _dt.datetime.now(tz=_dt.timezone.utc).isoformat()
                log_store.record(
                    started_at=now,
                    finished_at=now,
                    type="scheduled",
                    status="skipped",
                    took_ms=0,
                    model=settings.embedding.model,
                    source_type=settings.source.type,
                    collection=settings.vector_store.collection,
                    inserted=0,
                    updated=0,
                    skipped_records=0,
                    errors=0,
                    error_message=None,
                    reason="sync_already_running",
                )
            return
        # Lock acquired — _run_sync_bg will release it in finally
        _run_sync_bg(app_state, mode="incremental", triggered_by="scheduler")

    scheduler = BackgroundScheduler()
    trigger = CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
    )
    scheduler.add_job(_scheduled_job, trigger=trigger, id="incremental_sync",
                      replace_existing=True, misfire_grace_time=60)
    scheduler.start()
    logger.info("Scheduler started with cron=%r", schedule)
    return scheduler
