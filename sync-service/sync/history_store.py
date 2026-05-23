"""HistoryStore — persists per-user search history in SQLite at /app/.sync/search_history.db.

Uses stdlib sqlite3 only (D-01). Schema: search_history table with all fields required by D-05.
Index on (user_id, collection, timestamp DESC) for fast per-user lookups (D-06).
Purges oldest rows beyond max_per_user (default 500) on each insert (D-07).
log() wraps in try/except for graceful degradation — search proceeds on SQLite failure (D-12).
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_PATH = Path("/app/.sync/search_history.db")

_MAX_PER_USER = 500

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS search_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT    NOT NULL,
    query        TEXT    NOT NULL,
    collection   TEXT    NOT NULL DEFAULT '',
    filters      TEXT    NOT NULL DEFAULT '',
    min_score    REAL,
    result_count INTEGER NOT NULL DEFAULT 0,
    timestamp    TEXT    NOT NULL
);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_history_user_col_time
    ON search_history(user_id, collection, timestamp DESC);
"""

_INSERT_SQL = """
INSERT INTO search_history (user_id, query, collection, filters, min_score, result_count, timestamp)
VALUES (?, ?, ?, ?, ?, ?, ?);
"""

_PURGE_SQL = """
DELETE FROM search_history
WHERE user_id = ?
  AND id NOT IN (
      SELECT id FROM search_history
      WHERE user_id = ?
      ORDER BY timestamp DESC
      LIMIT ?
  );
"""


class HistoryStore:
    """Thread-safe SQLite-backed store for per-user search history."""

    def __init__(self, path: Path = _DB_PATH) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: FastAPI background tasks run in threads
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_INDEX)
        self._conn.commit()
        logger.info("HistoryStore initialised at %s", self._path)

    def log(
        self,
        *,
        user_id: str,
        query: str,
        collection: str,
        filters: str,
        min_score: float | None,
        result_count: int,
        timestamp: str,
    ) -> None:
        """Insert a search history row and purge oldest rows beyond max_per_user.

        Wrapped in try/except for graceful degradation (D-12): if SQLite fails,
        a WARNING is logged but the search result is not affected.
        """
        try:
            self._conn.execute(
                _INSERT_SQL,
                (user_id, query, collection, filters, min_score, result_count, timestamp),
            )
            # Purge oldest rows beyond cap (D-07)
            self._conn.execute(_PURGE_SQL, (user_id, user_id, _MAX_PER_USER))
            self._conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "HistoryStore.log failed for user %r — search result unaffected: %s",
                user_id,
                exc,
            )

    def get_history(self, user_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return up to *limit* rows (max 500) for the user, newest first."""
        limit = min(max(1, limit), 500)
        cursor = self._conn.execute(
            "SELECT id, user_id, query, collection, filters, min_score, result_count, timestamp "
            "FROM search_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        )
        return [dict(row) for row in cursor.fetchall()]

    def delete_history(self, user_id: str) -> None:
        """Delete all history rows for the given user_id."""
        self._conn.execute("DELETE FROM search_history WHERE user_id = ?", (user_id,))
        self._conn.commit()

    def get_suggestions(self, user_id: str, q: str, limit: int = 10) -> list[str]:
        """Return distinct query strings matching *q* as prefix, newest first.

        Uses LIKE 'q%' on the query field (D-15).
        """
        limit = min(max(1, limit), 20)
        cursor = self._conn.execute(
            "SELECT DISTINCT query FROM search_history "
            "WHERE user_id = ? AND query LIKE ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (user_id, q + "%", limit),
        )
        return [row["query"] for row in cursor.fetchall()]

    def close(self) -> None:
        """Close the SQLite connection (called at shutdown)."""
        self._conn.close()
