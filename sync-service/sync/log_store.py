"""LogStore — persists sync run records in SQLite at /app/.sync/sync_logs.db.

Uses stdlib sqlite3 only (D-01). Schema: sync_runs table with all fields
required by D-02. Prunes to latest 1000 rows after each insert (D-05).
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DB_PATH = Path("/app/.sync/sync_logs.db")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS sync_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT    NOT NULL,
    type            TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    took_ms         INTEGER NOT NULL DEFAULT 0,
    model           TEXT    NOT NULL DEFAULT '',
    source_type     TEXT    NOT NULL DEFAULT '',
    collection      TEXT    NOT NULL DEFAULT '',
    inserted        INTEGER NOT NULL DEFAULT 0,
    updated         INTEGER NOT NULL DEFAULT 0,
    skipped_records INTEGER NOT NULL DEFAULT 0,
    errors          INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    reason          TEXT
);
"""

_PRUNE_SQL = """
DELETE FROM sync_runs
WHERE id NOT IN (
    SELECT id FROM sync_runs
    ORDER BY id DESC
    LIMIT 1000
);
"""

_INSERT_SQL = """
INSERT INTO sync_runs
    (started_at, finished_at, type, status, took_ms, model, source_type,
     collection, inserted, updated, skipped_records, errors,
     error_message, reason)
VALUES
    (:started_at, :finished_at, :type, :status, :took_ms, :model,
     :source_type, :collection, :inserted, :updated, :skipped_records,
     :errors, :error_message, :reason);
"""

_COLUMNS = [
    "id", "started_at", "finished_at", "type", "status", "took_ms",
    "model", "source_type", "collection", "inserted", "updated",
    "skipped_records", "errors", "error_message", "reason",
]


class LogStore:
    """Thread-safe SQLite-backed log of sync runs."""

    def __init__(self, path: Path = _DB_PATH) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: FastAPI background tasks run in threads
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()
        logger.info("LogStore initialised at %s", self._path)

    def record(
        self,
        *,
        started_at: str,
        finished_at: str,
        type: str,
        status: str,
        took_ms: int = 0,
        model: str = "",
        source_type: str = "",
        collection: str = "",
        inserted: int = 0,
        updated: int = 0,
        skipped_records: int = 0,
        errors: int = 0,
        error_message: str | None = None,
        reason: str | None = None,
    ) -> int:
        """Insert a sync run record. Prunes to 1000 rows synchronously (D-05).

        Returns the new row id.
        """
        params: dict[str, Any] = {
            "started_at": started_at,
            "finished_at": finished_at,
            "type": type,
            "status": status,
            "took_ms": took_ms,
            "model": model,
            "source_type": source_type,
            "collection": collection,
            "inserted": inserted,
            "updated": updated,
            "skipped_records": skipped_records,
            "errors": errors,
            "error_message": error_message,
            "reason": reason,
        }
        cursor = self._conn.execute(_INSERT_SQL, params)
        row_id = cursor.lastrowid
        # Prune synchronously (D-05)
        self._conn.execute(_PRUNE_SQL)
        self._conn.commit()
        return row_id  # type: ignore[return-value]

    def get_logs(
        self,
        limit: int = 20,
        status: str | None = None,
        collection: str | None = None,
    ) -> list[dict]:
        """Return up to *limit* rows (max 100), newest first.

        Optional filters:
          - *status*: 'completed' | 'failed' | 'skipped'
          - *collection*: collection name to filter by (Phase 11, D-25)
        """
        limit = min(max(1, limit), 100)
        clauses: list[str] = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if collection:
            clauses.append("collection = ?")
            params.append(collection)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        cursor = self._conn.execute(
            f"SELECT * FROM sync_runs {where} ORDER BY started_at DESC LIMIT ?",
            params,
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_latest(self) -> dict | None:
        """Return the single most-recent row, or None if table is empty."""
        cursor = self._conn.execute(
            "SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        """Close the SQLite connection (called at shutdown)."""
        self._conn.close()
