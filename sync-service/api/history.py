"""History API router — GET /history, DELETE /history.

GET /history  — returns the calling user's search history, paginated, newest first.
DELETE /history — deletes all history for the calling user (GDPR right to erasure, D-14).

Both endpoints scope to the authenticated user's JWT sub (T-13-01, T-13-02):
user_id is always derived from the validated JWT payload — never from query params.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from auth.dependencies import get_current_user
from auth.user_store import UserRecord

router = APIRouter(prefix="/history", tags=["history"])


@router.get("")
async def get_history(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    user: UserRecord = Depends(get_current_user),
) -> list[dict]:
    """Return the authenticated user's search history, newest first.

    Query params:
      - limit: number of rows to return (1-500, default 50)
      - offset: pagination offset (default 0)
    """
    history_store = request.app.state.history_store
    return history_store.get_history(user.username, limit=limit, offset=offset)


@router.delete("", status_code=204)
async def delete_history(
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Response:
    """Delete all history for the authenticated user (D-14, GDPR right to erasure)."""
    history_store = request.app.state.history_store
    history_store.delete_history(user.username)
    return Response(status_code=204)
