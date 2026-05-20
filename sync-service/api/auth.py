"""Auth router — POST /auth/login, /auth/refresh, /auth/logout,
/auth/totp/setup, /auth/totp/verify, /auth/totp/confirm.

All /auth/login, /auth/refresh, /auth/logout, /auth/totp/confirm are PUBLIC
(no JWT required). /auth/totp/setup and /auth/totp/verify require JWT admin (D-14).
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from auth.dependencies import get_current_user, require_admin
from auth.user_store import UserRecord

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_limiter = Limiter(key_func=get_remote_address)

_JWT_SECRET = os.getenv("JWT_SECRET") or ""
# Validated in main.py lifespan before the server starts accepting requests (CR-01).
_ALGORITHM = "HS256"
_ACCESS_TTL_MINUTES = 15
_REFRESH_TTL_DAYS = 7
_TMP_TOKEN_TTL_SECONDS = 120  # 2 minutes (D-08)


# --- Pydantic models ---------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class TOTPVerifyRequest(BaseModel):
    totp_code: str


class TOTPConfirmRequest(BaseModel):
    tmp_token: str
    totp_code: str


# --- Token helpers -----------------------------------------------------------

def _make_access_token(username: str, role: str) -> str:
    exp = datetime.now(tz=timezone.utc) + timedelta(minutes=_ACCESS_TTL_MINUTES)
    payload = {"sub": username, "role": role, "exp": exp, "jti": str(uuid.uuid4())}
    return jwt.encode(payload, _JWT_SECRET, algorithm=_ALGORITHM)


def _make_refresh_token() -> str:
    return str(uuid.uuid4()) + "-" + str(uuid.uuid4())


def _make_tmp_token() -> str:
    return str(uuid.uuid4())


# --- Helpers -----------------------------------------------------------------

def _purge_expired_tmp_tokens(tmp_tokens: dict) -> None:
    """Remove all expired entries from the tmp_tokens dict (lazy GC)."""
    now = datetime.now(tz=timezone.utc)
    expired = [k for k, v in tmp_tokens.items() if now > v["expires_at"]]
    for k in expired:
        tmp_tokens.pop(k, None)


# --- Endpoints ---------------------------------------------------------------

@router.post("/login")
@_limiter.limit("10/minute")
async def login(body: LoginRequest, request: Request) -> dict:
    """Authenticate user. Returns tokens or totp_required challenge (D-08)."""
    _purge_expired_tmp_tokens(request.app.state.tmp_tokens)
    user_store = request.app.state.user_store
    user = user_store.verify_password(body.username, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenziali non valide",
        )

    # User with TOTP enabled → return tmp_token challenge
    if user.totp_enabled:
        tmp_token = _make_tmp_token()
        expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=_TMP_TOKEN_TTL_SECONDS)
        request.app.state.tmp_tokens[tmp_token] = {
            "username": user.username,
            "expires_at": expires_at,
        }
        return {"status": "totp_required", "tmp_token": tmp_token}

    # Regular login → issue tokens
    access_token = _make_access_token(user.username, user.role)
    raw_refresh = _make_refresh_token()
    refresh_exp = datetime.now(tz=timezone.utc) + timedelta(days=_REFRESH_TTL_DAYS)
    token_store = request.app.state.token_store
    token_store.save(raw_refresh, user.id, refresh_exp.isoformat())
    return {
        "access_token": access_token,
        "refresh_token": raw_refresh,
        "token_type": "bearer",
    }


@router.post("/refresh")
async def refresh_token(body: RefreshRequest, request: Request) -> dict:
    """Exchange a valid refresh token for a new access token and a new refresh token."""
    token_store = request.app.state.token_store
    row = token_store.get_valid(body.refresh_token)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token non valido o scaduto",
        )
    # Rotate: revoke consumed token before issuing a new one
    token_store.revoke(body.refresh_token)
    user_store = request.app.state.user_store
    user = user_store.get_by_id(row["user_id"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Utente non trovato")
    access_token = _make_access_token(user.username, user.role)
    raw_refresh = _make_refresh_token()
    refresh_exp = datetime.now(tz=timezone.utc) + timedelta(days=_REFRESH_TTL_DAYS)
    token_store.save(raw_refresh, user.id, refresh_exp.isoformat())
    return {
        "access_token": access_token,
        "refresh_token": raw_refresh,
        "token_type": "bearer",
    }


@router.post("/logout")
async def logout(body: LogoutRequest, request: Request) -> dict:
    """Revoke refresh token (D-11 server-side logout)."""
    token_store = request.app.state.token_store
    token_store.revoke(body.refresh_token)
    return {"status": "logged_out"}


@router.post("/totp/setup")
async def totp_setup(
    request: Request,
    user: UserRecord = Depends(require_admin),
) -> dict:
    """Generate TOTP secret and QR URI for the authenticated admin."""
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    qr_uri = totp.provisioning_uri(name=user.username, issuer_name="smart-search")
    # Persist secret (not yet enabled — enabled after verify)
    user_store = request.app.state.user_store
    user_store.update_user(user.username, totp_secret=secret, totp_enabled=False)
    return {"secret": secret, "qr_uri": qr_uri}


@router.post("/totp/verify")
async def totp_verify(
    body: TOTPVerifyRequest,
    request: Request,
    user: UserRecord = Depends(require_admin),
) -> dict:
    """Confirm first TOTP code to enable 2FA (D-07)."""
    user_store = request.app.state.user_store
    current = user_store.get_by_username(user.username)
    if current is None or current.totp_secret is None:
        raise HTTPException(status_code=400, detail="TOTP non configurato. Chiama prima /auth/totp/setup.")
    totp = pyotp.TOTP(current.totp_secret)
    if not totp.verify(body.totp_code, valid_window=1):
        raise HTTPException(status_code=400, detail="Codice TOTP non valido")
    user_store.update_user(user.username, totp_enabled=True)
    return {"status": "totp_enabled"}


@router.post("/totp/confirm")
async def totp_confirm(body: TOTPConfirmRequest, request: Request) -> dict:
    """Complete TOTP login flow using tmp_token + totp_code (D-08)."""
    tmp_tokens: dict = request.app.state.tmp_tokens
    _purge_expired_tmp_tokens(tmp_tokens)
    entry = tmp_tokens.get(body.tmp_token)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessione scaduta. Rieffettua il login.",
        )
    now = datetime.now(tz=timezone.utc)
    if now > entry["expires_at"]:
        tmp_tokens.pop(body.tmp_token, None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessione scaduta. Rieffettua il login.",
        )
    user_store = request.app.state.user_store
    user = user_store.get_by_username(entry["username"])
    if user is None or user.totp_secret is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Utente non trovato")
    totp = pyotp.TOTP(user.totp_secret)
    if not totp.verify(body.totp_code, valid_window=1):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Codice non valido o scaduto. Riprova.")
    # Consume the tmp_token
    tmp_tokens.pop(body.tmp_token, None)
    access_token = _make_access_token(user.username, user.role)
    raw_refresh = _make_refresh_token()
    refresh_exp = datetime.now(tz=timezone.utc) + timedelta(days=_REFRESH_TTL_DAYS)
    token_store = request.app.state.token_store
    token_store.save(raw_refresh, user.id, refresh_exp.isoformat())
    return {
        "access_token": access_token,
        "refresh_token": raw_refresh,
        "token_type": "bearer",
    }
