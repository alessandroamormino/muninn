"""Logs API router — GET /logs/sync, GET /logs/sync/latest.

Exposes the LogStore attached to app.state.log_store (initialised in lifespan).
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from auth.dependencies import get_current_user
from auth.user_store import UserRecord

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("/sync")
async def get_sync_logs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query(pattern="^(completed|failed|skipped)$")] = None,
    collection: Annotated[str | None, Query()] = None,
    _: UserRecord = Depends(get_current_user),
) -> list[dict]:
    """Return sync run history, newest first.

    Query params:
      - limit: number of rows to return (1-100, default 20)
      - status: filter by status — 'completed', 'failed', or 'skipped'
      - collection: filter by collection name (Phase 11, D-25)
    """
    log_store = request.app.state.log_store
    return log_store.get_logs(limit=limit, status=status, collection=collection)


@router.get("/sync/latest")
async def get_latest_sync(request: Request, _: UserRecord = Depends(get_current_user)) -> dict | None:
    """Return the single most-recent sync run record, or null if no runs yet."""
    log_store = request.app.state.log_store
    return log_store.get_latest()
