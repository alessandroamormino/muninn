"""UserStore and RefreshTokenStore — SQLite-backed, replicating log_store.py pattern.

DB path: /app/.sync/users.db (same volume as sync_logs.db).
WAL mode, check_same_thread=False — safe for FastAPI background threads (D-03).
Passwords hashed with bcrypt via passlib (D-04).
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from passlib.context import CryptContext

logger = logging.getLogger(__name__)

_DB_PATH = Path("/app/.sync/users.db")

_PWD_CTX = CryptContext(schemes=["bcrypt"], deprecated="auto")

_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT    UNIQUE NOT NULL,
    hashed_password TEXT   NOT NULL,
    role           TEXT    NOT NULL,
    totp_secret    TEXT,
    totp_enabled   BOOLEAN NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL,
    is_active      BOOLEAN NOT NULL DEFAULT 1
);
"""

_CREATE_REFRESH_TOKENS = """
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash  TEXT    PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    expires_at  TEXT    NOT NULL,
    revoked     BOOLEAN NOT NULL DEFAULT 0
);
"""


@dataclass
class UserRecord:
    id: int
    username: str
    hashed_password: str
    role: str
    totp_secret: Optional[str]
    totp_enabled: bool
    created_at: str
    is_active: bool


class UserStore:
    """Thread-safe SQLite store for user accounts."""

    def __init__(self, path: Path = _DB_PATH) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(_CREATE_USERS)
        self._conn.execute(_CREATE_REFRESH_TOKENS)
        self._conn.commit()
        logger.info("UserStore initialised at %s", self._path)

    # --- Query helpers -------------------------------------------------------

    def _row_to_user(self, row) -> UserRecord:
        return UserRecord(
            id=row["id"],
            username=row["username"],
            hashed_password=row["hashed_password"],
            role=row["role"],
            totp_secret=row["totp_secret"],
            totp_enabled=bool(row["totp_enabled"]),
            created_at=row["created_at"],
            is_active=bool(row["is_active"]),
        )

    def is_empty(self) -> bool:
        cur = self._conn.execute("SELECT COUNT(*) FROM users")
        return cur.fetchone()[0] == 0

    def get_by_username(self, username: str) -> Optional[UserRecord]:
        cur = self._conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        )
        row = cur.fetchone()
        return self._row_to_user(row) if row else None

    def create_user(self, username: str, password: str, role: str) -> UserRecord:
        if role not in ("reader", "admin"):
            raise ValueError(f"Invalid role: {role!r}. Must be 'reader' or 'admin'.")
        hashed = _PWD_CTX.hash(password)
        created_at = datetime.now(tz=timezone.utc).isoformat()
        try:
            self._conn.execute(
                "INSERT INTO users (username, hashed_password, role, created_at) VALUES (?, ?, ?, ?)",
                (username, hashed, role, created_at),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Username {username!r} already exists.") from exc
        row = self._conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return self._row_to_user(row)

    def verify_password(self, username: str, plain_password: str) -> Optional[UserRecord]:
        user = self.get_by_username(username)
        if user is None or not user.is_active:
            return None
        if not _PWD_CTX.verify(plain_password, user.hashed_password):
            return None
        return user

    def update_user(
        self,
        username: str,
        role: Optional[str] = None,
        password: Optional[str] = None,
        totp_secret: Optional[str] = None,
        totp_enabled: Optional[bool] = None,
    ) -> bool:
        fields: list[str] = []
        values: list = []
        if role is not None:
            if role not in ("reader", "admin"):
                raise ValueError(f"Invalid role: {role!r}")
            fields.append("role = ?")
            values.append(role)
        if password is not None:
            fields.append("hashed_password = ?")
            values.append(_PWD_CTX.hash(password))
        if totp_secret is not None:
            fields.append("totp_secret = ?")
            values.append(totp_secret)
        if totp_enabled is not None:
            fields.append("totp_enabled = ?")
            values.append(int(totp_enabled))
        if not fields:
            return False
        values.append(username)
        self._conn.execute(
            f"UPDATE users SET {', '.join(fields)} WHERE username = ?", values
        )
        self._conn.commit()
        return self._conn.total_changes > 0

    def deactivate_user(self, username: str) -> bool:
        self._conn.execute(
            "UPDATE users SET is_active = 0 WHERE username = ?", (username,)
        )
        self._conn.commit()
        return self._conn.total_changes > 0

    def list_users(self) -> list[UserRecord]:
        cur = self._conn.execute(
            "SELECT * FROM users ORDER BY id ASC"
        )
        return [self._row_to_user(row) for row in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RefreshTokenStore:
    """Manages refresh_tokens table in users.db (same connection as UserStore).

    Accepts the shared sqlite3 connection from UserStore for atomicity.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(self, raw_token: str, user_id: int, expires_at: str) -> None:
        token_hash = _sha256(raw_token)
        self._conn.execute(
            "INSERT OR REPLACE INTO refresh_tokens (token_hash, user_id, expires_at, revoked) VALUES (?, ?, ?, 0)",
            (token_hash, user_id, expires_at),
        )
        self._conn.commit()

    def get_valid(self, raw_token: str) -> Optional[sqlite3.Row]:
        """Return token row if not revoked and not expired; None otherwise."""
        token_hash = _sha256(raw_token)
        now = datetime.now(tz=timezone.utc).isoformat()
        cur = self._conn.execute(
            "SELECT * FROM refresh_tokens WHERE token_hash = ? AND revoked = 0 AND expires_at > ?",
            (token_hash, now),
        )
        return cur.fetchone()

    def revoke(self, raw_token: str) -> bool:
        token_hash = _sha256(raw_token)
        self._conn.execute(
            "UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?",
            (token_hash,),
        )
        self._conn.commit()
        return self._conn.total_changes > 0
