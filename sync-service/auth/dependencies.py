"""FastAPI dependency functions for JWT authentication (D-18).

get_current_user: decodes Bearer JWT, returns UserRecord, raises 401 on failure.
require_admin: wraps get_current_user, raises 403 if role != 'admin'.

JWT_SECRET loaded from env at import time — never hardcoded (D-12).
Algorithm: HS256 (D-13).
"""
from __future__ import annotations

import logging
import os

from fastapi import Depends, HTTPException, Request, status
from jose import ExpiredSignatureError, JWTError, jwt

from auth.user_store import UserRecord

logger = logging.getLogger(__name__)

_JWT_SECRET = os.getenv("JWT_SECRET") or ""
# Validated in main.py lifespan before the server starts accepting requests (CR-01).
_ALGORITHM = "HS256"


def get_current_user(request: Request) -> UserRecord:
    """Decode JWT from Authorization: Bearer header.

    Raises:
        401 — missing header, malformed token, or invalid signature
        401 with detail 'Token scaduto' — token expired (D-19)
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_header[len("Bearer "):]
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_ALGORITHM])
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token scaduto",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token non valido",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username: str | None = payload.get("sub")
    role: str | None = payload.get("role")
    if not username or not role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token non valido",
        )

    user_store = getattr(request.app.state, "user_store", None)
    if user_store is None:
        raise HTTPException(status_code=500, detail="UserStore not initialised")

    user = user_store.get_by_username(username)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utente non trovato o disattivato",
        )
    return user


def require_admin(user: UserRecord = Depends(get_current_user)) -> UserRecord:
    """Raise 403 if the authenticated user is not an admin (D-17)."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accesso riservato agli amministratori",
        )
    return user
