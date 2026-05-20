"""Admin router — user management endpoints (D-15).

All endpoints require require_admin dependency.
POST /admin/users — create user
GET  /admin/users — list users (no hashed_password)
DELETE /admin/users/{username} — deactivate user (is_active=False)
PUT  /admin/users/{username} — update role and/or password
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auth.dependencies import require_admin
from auth.user_store import UserRecord

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str  # "reader" | "admin"


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    password: Optional[str] = None


@router.post("/users", status_code=201)
async def create_user(
    body: CreateUserRequest,
    request: Request,
    _: UserRecord = Depends(require_admin),
) -> dict:
    """Create a new user. Role must be 'reader' or 'admin'."""
    user_store = request.app.state.user_store
    try:
        user = user_store.create_user(body.username, body.password, body.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "created_at": user.created_at,
        "is_active": user.is_active,
    }


@router.get("/users")
async def list_users(
    request: Request,
    _: UserRecord = Depends(require_admin),
) -> dict:
    """List all users. hashed_password is never included in the response."""
    user_store = request.app.state.user_store
    users = user_store.list_users()
    return {
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "totp_enabled": u.totp_enabled,
                "created_at": u.created_at,
                "is_active": u.is_active,
            }
            for u in users
        ]
    }


@router.delete("/users/{username}", status_code=200)
async def deactivate_user(
    username: str,
    request: Request,
    _: UserRecord = Depends(require_admin),
) -> dict:
    """Deactivate user (sets is_active=False). Does not delete the record."""
    user_store = request.app.state.user_store
    ok = user_store.deactivate_user(username)
    if not ok:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    return {"status": "deactivated", "username": username}


@router.put("/users/{username}")
async def update_user(
    username: str,
    body: UpdateUserRequest,
    request: Request,
    _: UserRecord = Depends(require_admin),
) -> dict:
    """Update user role and/or password."""
    user_store = request.app.state.user_store
    if user_store.get_by_username(username) is None:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    try:
        user_store.update_user(username, role=body.role, password=body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    updated = user_store.get_by_username(username)
    return {
        "id": updated.id,
        "username": updated.username,
        "role": updated.role,
        "is_active": updated.is_active,
    }
