"""Backup management endpoints — POST /backup/{name}, POST /backup/{name}/restore,
GET /backup, GET /backup/status, DELETE /backup/{bundle_id}.

All mutating endpoints require admin. Status is readable by any authenticated user.
Restore is additionally gated by confirm=true (D-08: destructive action).

Long-running ops (backup, restore) run as FastAPI BackgroundTasks reusing
app.state.sync_lock (same lock as sync/unload/load — all mutate Qdrant).
Progress is tracked on a SEPARATE app.state.backup_progress dict (NOT
sync_progress/unload_progress — Pitfall 6 of Phase 26).

Lock-ownership: the endpoint acquires the lock; the background task owns
the release (in its finally block). Lock released only if held (Pitfall 9).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from auth.dependencies import get_current_user, require_admin
from auth.user_store import UserRecord

# Reuse the existing path-traversal guard and config resolution helper —
# single source of truth, do NOT redefine the regex (T-26-05).
from api.upload import _COLLECTION_RE, _resolve_config_path
from backup.manager import run_backup, run_restore
from backup.s3_client import S3BackupClient
from config.settings import settings

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------

def _run_backup_bg(app_state, collection: str) -> None:
    """Snapshot → upload → catalog → prune. Releases sync_lock in finally (Pitfall 9)."""
    try:
        app_state.backup_progress = {"collection": collection, "phase": "snapshotting"}
        s3 = S3BackupClient(settings.backup.s3)
        keep_n = settings.backup.keep_n
        app_state.backup_progress = {"collection": collection, "phase": "uploading"}
        manifest = run_backup(
            app_state.vector_store,
            s3,
            app_state.backup_catalog,
            collection,
            keep_n,
        )
        app_state.backup_progress = {
            "collection": collection,
            "phase": "done",
            "bundle_id": manifest["bundle_id"],
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Backup failed for %r: %s", collection, exc, exc_info=True)
        app_state.backup_progress = {"collection": collection, "phase": "failed", "error": str(exc)}
    finally:
        if app_state.sync_lock.locked():
            app_state.sync_lock.release()


def _run_restore_bg(app_state, collection: str, bundle_id: str) -> None:
    """Download snapshot → restore_collection. Releases sync_lock in finally (Pitfall 9)."""
    try:
        app_state.backup_progress = {"collection": collection, "phase": "restoring", "bundle_id": bundle_id}
        s3 = S3BackupClient(settings.backup.s3)
        run_restore(
            app_state.vector_store,
            s3,
            app_state.backup_catalog,
            collection,
            bundle_id,
        )
        app_state.backup_progress = {"collection": collection, "phase": "done", "bundle_id": bundle_id}
    except Exception as exc:  # noqa: BLE001
        logger.error("Restore failed for %r bundle %r: %s", collection, bundle_id, exc, exc_info=True)
        app_state.backup_progress = {
            "collection": collection,
            "phase": "failed",
            "bundle_id": bundle_id,
            "error": str(exc),
        }
    finally:
        if app_state.sync_lock.locked():
            app_state.sync_lock.release()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/backup/{name}")
async def trigger_backup(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str,
    _: UserRecord = Depends(require_admin),
) -> dict:
    """Trigger an off-host backup of collection *name* (BAK-01)."""
    if not _COLLECTION_RE.match(name):
        raise HTTPException(status_code=422, detail="Invalid collection name")
    if _resolve_config_path(name) is None:
        raise HTTPException(status_code=404, detail=f"No config found for collection '{name}'")

    lock_acquired = False
    try:
        if not request.app.state.sync_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Sync/backup/unload already in progress")
        lock_acquired = True
        background_tasks.add_task(_run_backup_bg, request.app.state, name)
        lock_acquired = False  # background task now owns the lock release
    except Exception:
        if lock_acquired:
            request.app.state.sync_lock.release()
        raise
    return {"status": "started"}


@router.post("/backup/{name}/restore")
async def restore_backup(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str,
    bundle_id: str = Query(..., description="Bundle ID to restore"),
    confirm: bool = Query(False, description="Must be true to confirm destructive restore (D-08)"),
    _: UserRecord = Depends(require_admin),
) -> dict:
    """Restore collection *name* from bundle *bundle_id* (BAK-02, D-08).

    Destructive action: overwrites the live collection. Requires confirm=true.
    """
    if not confirm:
        raise HTTPException(status_code=400, detail="Restore is destructive; pass confirm=true")
    if not _COLLECTION_RE.match(name):
        raise HTTPException(status_code=422, detail="Invalid collection name")
    if not _COLLECTION_RE.match(bundle_id):
        raise HTTPException(status_code=422, detail="Invalid bundle_id")
    if _resolve_config_path(name) is None:
        raise HTTPException(status_code=404, detail=f"No config found for collection '{name}'")

    lock_acquired = False
    try:
        if not request.app.state.sync_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Sync/backup/unload already in progress")
        lock_acquired = True
        background_tasks.add_task(_run_restore_bg, request.app.state, name, bundle_id)
        lock_acquired = False  # background task now owns the lock release
    except Exception:
        if lock_acquired:
            request.app.state.sync_lock.release()
        raise
    return {"status": "started"}


@router.get("/backup")
async def list_backups(
    request: Request,
    _: UserRecord = Depends(require_admin),
) -> dict:
    """List all backup bundle entries in the catalog (BAK-04)."""
    return request.app.state.backup_catalog.all()


@router.get("/backup/status")
async def backup_status(
    request: Request,
    _: UserRecord = Depends(get_current_user),
) -> dict:
    """Return the current backup_progress dict (readable by any authenticated user).

    Shape: {"collection": str, "phase": "uploading"|"restoring"|"done"|"failed", ...}
    Returns {} when no backup/restore operation has run yet.
    """
    return getattr(request.app.state, "backup_progress", None) or {}


@router.delete("/backup/{bundle_id}")
async def delete_backup(
    request: Request,
    bundle_id: str,
    _: UserRecord = Depends(require_admin),
) -> dict:
    """Delete a backup bundle: remove all three S3 keys + catalog entry."""
    if not _COLLECTION_RE.match(bundle_id):
        raise HTTPException(status_code=422, detail="Invalid bundle_id")

    catalog = request.app.state.backup_catalog
    entry = catalog.get(bundle_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Bundle '{bundle_id}' not found in catalog")

    s3 = S3BackupClient(settings.backup.s3)
    for s3_key in entry.get("s3_keys", {}).values():
        try:
            s3.delete(s3_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("S3 delete failed for %r: %s", s3_key, exc)

    catalog.remove(bundle_id)
    return {"status": "deleted"}
