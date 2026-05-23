"""CacheStore — exact-match SQLite cache for /search results with TTL.

Uses the same DB as HistoryStore (/app/.sync/search_history.db) to avoid
managing a second file. WAL mode (set by HistoryStore at init) makes
concurrent reads from search and writes from sync safe.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_PATH = Path("/app/.sync/search_history.db")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS search_cache (
    cache_key    TEXT PRIMARY KEY,
    collection   TEXT NOT NULL,
    results_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);
"""


def make_cache_key(q: str, collection: str, filters: str | None, min_score: float | None) -> str:
    """Deterministic SHA256 key per D-09."""
    raw = f"{q}|{collection}|{filters or ''}|{min_score or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()


class CacheStore:
    """Thread-safe SQLite-backed exact-match cache with per-collection TTL (D-08..D-12)."""

    def __init__(self, path: Path = _DB_PATH, ttl_seconds: int = 300) -> None:
        self._path = path
        self._ttl = ttl_seconds
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()
        logger.info("CacheStore initialised at %s (ttl=%ds)", self._path, self._ttl)

    def get(self, cache_key: str) -> dict | None:
        try:
            now = datetime.now(tz=timezone.utc).isoformat()
            row = self._conn.execute(
                "SELECT results_json, expires_at FROM search_cache WHERE cache_key=?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= now:
                # Expired — evict
                self._conn.execute("DELETE FROM search_cache WHERE cache_key=?", (cache_key,))
                self._conn.commit()
                return None
            return json.loads(row["results_json"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("CacheStore.get failed: %s", exc)
            return None

    def set(self, cache_key: str, collection: str, results: dict, ttl_seconds: int | None = None) -> None:
        try:
            ttl = ttl_seconds if ttl_seconds is not None else self._ttl
            now = datetime.now(tz=timezone.utc)
            expires = (now + timedelta(seconds=ttl)).isoformat()
            self._conn.execute(
                "INSERT OR REPLACE INTO search_cache "
                "(cache_key, collection, results_json, created_at, expires_at) VALUES (?,?,?,?,?)",
                (cache_key, collection, json.dumps(results), now.isoformat(), expires),
            )
            self._conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("CacheStore.set failed: %s", exc)

    def invalidate_collection(self, collection: str) -> None:
        try:
            self._conn.execute("DELETE FROM search_cache WHERE collection=?", (collection,))
            self._conn.commit()
            logger.info("CacheStore: invalidated all entries for collection %r", collection)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CacheStore.invalidate_collection failed: %s", exc)

    def close(self) -> None:
        self._conn.close()
