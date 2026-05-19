"""Sync API router — POST /sync, POST /sync/full, GET /sync/status.

Il sync è non-bloccante: viene eseguito come BackgroundTask FastAPI.
Un threading.Lock su app.state evita sync concorrenti (thread-safe).
"""
from __future__ import annotations

import datetime as _dt
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from config.settings import settings

logger = logging.getLogger(__name__)
router = APIRouter()


def _run_sync_bg(app_state, mode: str, triggered_by: str = "api") -> None:
    """Eseguito nel thread background da FastAPI. mode='incremental' o 'full'."""
    engine = app_state.sync_engine
    _t0 = time.perf_counter()
    _started_at = _dt.datetime.now(tz=_dt.timezone.utc).isoformat()
    _log_store = getattr(app_state, "log_store", None)

    # Determine log type: 'full', 'incremental', or 'scheduled'
    if triggered_by == "scheduler":
        _log_type = "scheduled"
    elif mode == "full":
        _log_type = "full"
    else:
        _log_type = "incremental"

    try:
        if mode == "full":
            result = engine.run_full()
        else:
            result = engine.run_incremental()
        app_state.sync_status = {
            "status": "completed",
            "last_run": {**result},
        }
        took_ms = int((time.perf_counter() - _t0) * 1000)
        if _log_store is not None:
            _finished_at = _dt.datetime.now(tz=_dt.timezone.utc).isoformat()
            _log_store.record(
                started_at=_started_at,
                finished_at=_finished_at,
                type=_log_type,
                status="completed",
                took_ms=took_ms,
                model=settings.embedding.model,
                source_type=settings.source.type,
                collection=settings.weaviate.collection,
                inserted=result.get("inserted", 0),
                updated=result.get("updated", 0),
                skipped_records=result.get("skipped", 0),
                errors=result.get("errors", 0),
                error_message=None,
                reason=None,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("Sync (%s) fallito: %s", mode, exc, exc_info=True)
        took_ms = int((time.perf_counter() - _t0) * 1000)
        app_state.sync_status = {
            "status": "failed",
            "last_run": {
                "error": str(exc),
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            },
        }
        if _log_store is not None:
            _finished_at = _dt.datetime.now(tz=_dt.timezone.utc).isoformat()
            _log_store.record(
                started_at=_started_at,
                finished_at=_finished_at,
                type=_log_type,
                status="failed",
                took_ms=took_ms,
                model=settings.embedding.model,
                source_type=settings.source.type,
                collection=settings.weaviate.collection,
                inserted=0,
                updated=0,
                skipped_records=0,
                errors=1,
                error_message=str(exc),
                reason=None,
            )
    finally:
        # Recompute took_ms for sync_status dict (backward compat)
        _took_ms_final = int((time.perf_counter() - _t0) * 1000)
        app_state.sync_status["last_run"]["took_ms"] = _took_ms_final
        app_state.sync_lock.release()


@router.post("/sync")
async def trigger_sync(request: Request, background_tasks: BackgroundTasks) -> dict:
    """Avvia un sync incrementale in background."""
    if not request.app.state.sync_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Sync already in progress")
    try:
        request.app.state.sync_status["status"] = "running"
        background_tasks.add_task(_run_sync_bg, request.app.state, "incremental")
    except Exception:
        request.app.state.sync_lock.release()
        raise
    return {"status": "started", "job": "incremental"}


@router.post("/sync/full")
async def trigger_full_sync(request: Request, background_tasks: BackgroundTasks) -> dict:
    """Avvia un full re-index in background."""
    if not request.app.state.sync_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Sync already in progress")
    try:
        request.app.state.sync_status["status"] = "running"
        background_tasks.add_task(_run_sync_bg, request.app.state, "full")
    except Exception:
        request.app.state.sync_lock.release()
        raise
    return {"status": "started", "job": "full"}


@router.get("/sync/status")
async def sync_status(request: Request) -> dict:
    """Restituisce lo stato corrente e le statistiche dell'ultimo sync."""
    return request.app.state.sync_status
