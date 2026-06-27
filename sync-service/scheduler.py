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
    """Create and return a started BackgroundScheduler, or None if both schedules are disabled.

    Sync job (if settings.sync.schedule is a cron expression):
      1. Tries sync_lock.acquire(blocking=False).
      2. If acquired  → calls _run_sync_bg(app_state, mode='incremental',
                         triggered_by='scheduler').
         _run_sync_bg owns the lock and releases it in its finally block.
      3. If not acquired → logs a warning; writes a skipped log entry via
                           app_state.log_store (if present).

    Backup job (if settings.backup.enabled and settings.backup.schedule is a cron
    expression, BAK-03): same non-blocking acquire discipline, distinct job id
    'scheduled_backup', calls _run_backup_bg from api.backup.

    Returns None only when BOTH sync and backup schedules are 'manual'/disabled.
    """
    sync_schedule = settings.sync.schedule
    backup_enabled = getattr(getattr(settings, "backup", None), "enabled", False)
    backup_schedule = getattr(getattr(settings, "backup", None), "schedule", "manual")

    sync_is_cron = is_cron_schedule(sync_schedule)
    backup_is_cron = backup_enabled and is_cron_schedule(backup_schedule)

    if not sync_is_cron and not backup_is_cron:
        logger.info(
            "Scheduler disabled (sync.schedule=%r, backup.schedule=%r). "
            "Set a cron expression to enable.",
            sync_schedule, backup_schedule,
        )
        return None

    # Lazy imports — only reached when at least one cron schedule is configured.
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BackgroundScheduler()

    if sync_is_cron:
        from api.sync import _run_sync_bg
        minute, hour, day, month, day_of_week = sync_schedule.strip().split()

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

        trigger = CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
        )
        scheduler.add_job(_scheduled_job, trigger=trigger, id="incremental_sync",
                          replace_existing=True, misfire_grace_time=60)

    if backup_is_cron:
        from api.backup import _run_backup_bg
        bminute, bhour, bday, bmonth, bday_of_week = backup_schedule.strip().split()
        _backup_collection = settings.vector_store.collection

        def _scheduled_backup_job():
            if not app_state.sync_lock.acquire(blocking=False):
                logger.warning("Scheduled backup fired but lock busy — skipping.")
                return
            # Lock acquired — _run_backup_bg will release it in finally.
            # settings here IS the scheduled entity's own config, so settings.backup
            # is that entity's per-entity backup block.
            _run_backup_bg(app_state, _backup_collection, settings.backup)

        backup_trigger = CronTrigger(
            minute=bminute,
            hour=bhour,
            day=bday,
            month=bmonth,
            day_of_week=bday_of_week,
        )
        scheduler.add_job(
            _scheduled_backup_job,
            trigger=backup_trigger,
            id="scheduled_backup",
            replace_existing=True,
            misfire_grace_time=60,
        )

    scheduler.start()
    logger.info(
        "Scheduler started — sync_cron=%r backup_cron=%r",
        sync_schedule if sync_is_cron else "disabled",
        backup_schedule if backup_is_cron else "disabled",
    )
    return scheduler
