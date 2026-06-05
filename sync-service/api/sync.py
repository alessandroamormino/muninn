"""Sync API router — POST /sync, POST /sync/full, GET /sync/status.

Il sync è non-bloccante: viene eseguito come BackgroundTask FastAPI.
Un threading.Lock su app.state evita sync concorrenti (thread-safe).
"""
from __future__ import annotations

import datetime as _dt
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from auth.dependencies import get_current_user, require_admin
from auth.user_store import UserRecord
from config.settings import load_config, settings
from sync.engine import SyncEngine

logger = logging.getLogger(__name__)
router = APIRouter()


def _run_sync_bg(app_state, mode: str, triggered_by: str = "api") -> None:
    """Eseguito nel thread background da FastAPI. mode='incremental' o 'full'."""
    # Reload config from disk on every sync so changes to config.yaml take effect
    # without restarting the container (e.g. after uploading a new file/config).
    fresh_settings = load_config()
    engine = SyncEngine(fresh_settings, app_state.vector_store, app_state.sync_engine._state)
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

    # --- Progress tracking callback (D-sync-progress) -----------------------
    app_state.sync_progress = {"phase": "fetching", "total": 0, "done": 0, "percent": 0.0,
                               "elapsed_seconds": 0, "eta_seconds": None}

    def _on_progress(phase: str, done: int, total: int) -> None:
        elapsed = time.perf_counter() - _t0
        percent = round(done / total * 100, 1) if total > 0 else 0.0
        eta = int(elapsed / done * (total - done)) if done > 0 else None
        app_state.sync_progress = {
            "phase": phase,
            "total": total,
            "done": done,
            "percent": percent,
            "elapsed_seconds": int(elapsed),
            "eta_seconds": eta,
            "resumable": True,
        }
    # -------------------------------------------------------------------------

    try:
        if mode == "full":
            result = engine.run_full(on_progress=_on_progress)
        else:
            result = engine.run_incremental(on_progress=_on_progress)
        app_state.sync_status = {
            "status": "completed",
            "last_run": {**result},
        }
        # --- Cache invalidation (SC-13-06) -----------------------------------------
        _cache_store = getattr(app_state, "cache_store", None)
        if _cache_store is not None:
            try:
                _cache_store.invalidate_collection(fresh_settings.weaviate.collection)
            except Exception as _inv_exc:  # noqa: BLE001
                logger.warning("Cache invalidation failed after sync: %s", _inv_exc)
        took_ms = int((time.perf_counter() - _t0) * 1000)
        if _log_store is not None:
            _finished_at = _dt.datetime.now(tz=_dt.timezone.utc).isoformat()
            _log_store.record(
                started_at=_started_at,
                finished_at=_finished_at,
                type=_log_type,
                status="completed",
                took_ms=took_ms,
                model=fresh_settings.embedding.model,
                source_type=fresh_settings.source.type,
                collection=fresh_settings.weaviate.collection,
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
                model=fresh_settings.embedding.model,
                source_type=fresh_settings.source.type,
                collection=fresh_settings.weaviate.collection,
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
        app_state.sync_progress = None  # clear progress when done
        app_state.sync_lock.release()


@router.post("/sync")
async def trigger_sync(request: Request, background_tasks: BackgroundTasks, _: UserRecord = Depends(require_admin)) -> dict:
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
async def trigger_full_sync(request: Request, background_tasks: BackgroundTasks, _: UserRecord = Depends(require_admin)) -> dict:
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
async def sync_status(request: Request, _: UserRecord = Depends(get_current_user)) -> dict:
    """Restituisce lo stato corrente e le statistiche dell'ultimo sync.

    Quando status='running', include un blocco 'progress' con:
      phase        — 'fetching' | 'embedding' | 'upserting'
      total        — record totali da processare
      done         — record già processati nella fase corrente
      percent      — percentuale completamento (0.0–100.0)
      elapsed_seconds — secondi trascorsi dall'inizio del sync
      eta_seconds  — stima secondi rimanenti (null se non ancora calcolabile)
    """
    status = dict(request.app.state.sync_status)
    if status.get("status") == "running":
        progress = getattr(request.app.state, "sync_progress", None)
        if progress is not None:
            status["progress"] = progress
    return status
