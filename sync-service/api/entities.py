"""Entity load/unload management — POST /collections/{name}/unload, /load,
GET /collections/{name}/load-status.

Unload(entity) = snapshot Qdrant collection (on the persistent qdrant_snapshots
volume) -> drop_index. Frees RAM; non-destructive (snapshot persists on disk).
Load(entity) = restore the collection from its registered snapshot. No
re-embedding (D-04 — Products cost ~$1.81 in OpenAI embedding tokens, never
re-pay that on every reload).

Both operations run as FastAPI BackgroundTasks, reusing the SAME
app.state.sync_lock used by sync (D-08/D-09): a sync in progress blocks
unload/load and vice versa, preventing concurrent mutation of the same Qdrant
collection. Progress is tracked on a SEPARATE app.state.unload_progress dict
(NOT sync_progress, Pitfall 6) so the frontend never confuses "sync running"
with "unload/load running".

Idempotency (Pitfall 5, TOCTOU): the background task re-checks
EntityStateStore.get_status() AFTER acquiring the lock, not just in the
endpoint, because a fast sequential pair of requests (not concurrent, but
back-to-back) could otherwise double-unload an already-unloaded collection or
overwrite a freshly re-synced active collection with a stale snapshot.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from auth.dependencies import get_current_user, require_admin
from auth.user_store import UserRecord

# Reuse the existing path-traversal guard and config resolution helper —
# single source of truth, do NOT redefine the regex (T-26-05).
from api.upload import _COLLECTION_RE, _resolve_config_path

logger = logging.getLogger(__name__)
router = APIRouter(tags=["collections"])


def _run_unload_bg(app_state, collection: str) -> None:
    """Background task: snapshot -> cleanup old snapshots -> drop_index -> set_unloaded.

    Idempotent: no-op if the entity is already 'unloaded' (Pitfall 5). Releases
    app_state.sync_lock in finally regardless of outcome (Pitfall 9 — no leaked lock).
    """
    state_store = app_state.entity_state_store
    vector_store = app_state.vector_store
    try:
        if state_store.get_status(collection) == "unloaded":
            logger.info("Entity %r already unloaded — no-op (Pitfall 5).", collection)
            return

        app_state.unload_progress = {"entity": collection, "phase": "snapshotting"}
        snapshot_name = vector_store.snapshot_collection(collection)

        app_state.unload_progress = {"entity": collection, "phase": "deleting"}
        # Cleanup duplicate snapshots BEFORE the drop (Pitfall 3) — if the drop
        # later fails, the newest snapshot remains the only/latest one on disk.
        for old in vector_store.list_collection_snapshots(collection):
            if old != snapshot_name:
                vector_store.delete_collection_snapshot(collection, old)

        vector_store.drop_index(collection)  # reuse existing method (D-07)

        state_store.set_unloaded(collection, snapshot_name, datetime.now(timezone.utc).isoformat())
        app_state.unload_progress = {"entity": collection, "phase": "done"}
    except Exception as exc:  # noqa: BLE001
        logger.error("Unload failed for %r: %s", collection, exc, exc_info=True)
        app_state.unload_progress = {"entity": collection, "phase": "failed", "error": str(exc)}
        # Status NOT changed — remains whatever it was before this attempt.
    finally:
        # Release only if held: the endpoint acquires the lock before scheduling
        # this task, but direct/unit invocation runs without it held (Pitfall 9).
        if app_state.sync_lock.locked():
            app_state.sync_lock.release()


def _run_load_bg(app_state, collection: str) -> None:
    """Background task: restore from registered snapshot -> set_active.

    Idempotent: no-op if the entity is already 'active' (Pitfall 5) — avoids
    overwriting a freshly-synced collection with a stale snapshot. Releases
    app_state.sync_lock in finally regardless of outcome.
    """
    state_store = app_state.entity_state_store
    vector_store = app_state.vector_store
    try:
        if state_store.get_status(collection) == "active":
            logger.info("Entity %r already active — no-op (Pitfall 5).", collection)
            return

        snapshot_name = state_store.get_snapshot_name(collection)
        if not snapshot_name:
            raise RuntimeError(f"No snapshot registered for {collection!r} — cannot load.")

        app_state.unload_progress = {"entity": collection, "phase": "restoring"}
        vector_store.restore_collection(collection, snapshot_name)  # creates or overwrites (Qdrant-native)

        state_store.set_active(collection, datetime.now(timezone.utc).isoformat())
        app_state.unload_progress = {"entity": collection, "phase": "done"}
    except Exception as exc:  # noqa: BLE001
        logger.error("Load failed for %r: %s", collection, exc, exc_info=True)
        app_state.unload_progress = {"entity": collection, "phase": "failed", "error": str(exc)}
        # Status stays 'unloaded' — still reactivable (Pitfall 4).
    finally:
        # Release only if held: the endpoint acquires the lock before scheduling
        # this task, but direct/unit invocation runs without it held (Pitfall 9).
        if app_state.sync_lock.locked():
            app_state.sync_lock.release()


@router.post("/collections/{name}/unload")
async def unload_collection(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str,
    _: UserRecord = Depends(require_admin),
) -> dict:
    """Schedule an unload (snapshot + delete) for the given entity. D-08/D-09/D-13."""
    if not _COLLECTION_RE.match(name):
        raise HTTPException(status_code=422, detail="Invalid collection name")
    if _resolve_config_path(name) is None:
        raise HTTPException(status_code=404, detail=f"No config found for collection '{name}'")

    lock_acquired = False
    try:
        if not request.app.state.sync_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Sync/unload/load already in progress")
        lock_acquired = True
        background_tasks.add_task(_run_unload_bg, request.app.state, name)
        lock_acquired = False  # background task now owns the lock release
    except Exception:
        if lock_acquired:
            request.app.state.sync_lock.release()
        raise
    return {"status": "started"}


@router.post("/collections/{name}/load")
async def load_collection(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str,
    _: UserRecord = Depends(require_admin),
) -> dict:
    """Schedule a load (restore from snapshot) for the given entity. D-08/D-09/D-13."""
    if not _COLLECTION_RE.match(name):
        raise HTTPException(status_code=422, detail="Invalid collection name")
    if _resolve_config_path(name) is None:
        raise HTTPException(status_code=404, detail=f"No config found for collection '{name}'")

    lock_acquired = False
    try:
        if not request.app.state.sync_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Sync/unload/load already in progress")
        lock_acquired = True
        background_tasks.add_task(_run_load_bg, request.app.state, name)
        lock_acquired = False  # background task now owns the lock release
    except Exception:
        if lock_acquired:
            request.app.state.sync_lock.release()
        raise
    return {"status": "started"}


@router.post("/collections/{name}/cache")
async def invalidate_collection_cache(
    request: Request,
    name: str,
    _: UserRecord = Depends(require_admin),
) -> dict:
    """Purge all cached search results for one entity (admin only).

    Useful after editing an entity's config.yaml / synonyms.yaml or its data: the
    exact-match cache (TTL 300s) is not invalidated on file edits, so without this a
    stale result can be served for up to the TTL. Fast synchronous DELETE — no lock,
    no background task (same call the sync path already runs after a re-index).
    """
    if not _COLLECTION_RE.match(name):
        raise HTTPException(status_code=422, detail="Invalid collection name")
    cache_store = getattr(request.app.state, "cache_store", None)
    if cache_store is None:
        return {"status": "noop", "detail": "cache disabled"}
    cache_store.invalidate_collection(name)
    return {"status": "purged", "collection": name}


@router.get("/collections/{name}/load-status")
async def collection_load_status(
    request: Request,
    name: str,
    _: UserRecord = Depends(get_current_user),
) -> dict:
    """Return the current unload/load progress dict (observable progress, SC-26-6).

    Shape: {"entity": str, "phase": "snapshotting"|"deleting"|"restoring"|"done"|"failed", "error"?: str}
    Returns {} when no unload/load operation has run yet (or app.state.unload_progress is unset).
    """
    return getattr(request.app.state, "unload_progress", None) or {}
