"""ExactMatchCacheAdapter — exact-match SQLite cache per i risultati di /search con TTL.

Refactoring di sync/cache_store.py (Phase 13) nel pattern adapter (Phase 13.1).
La logica interna è identica a CacheStore: SHA256 key, stessa tabella SQLite search_cache.
Zero regressioni per chi usa cache_mode: exact.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sync.cache_adapters.base import BaseCacheAdapter

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
    """Deterministic SHA256 key — re-esportata da cache_store.py per backward-compat."""
    raw = f"{q}|{collection}|{filters or ''}|{min_score or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()


class ExactMatchCacheAdapter(BaseCacheAdapter):
    """Thread-safe SQLite-backed exact-match cache con TTL per collection (D-03)."""

    def __init__(self, path: Path = _DB_PATH, ttl_seconds: int = 300) -> None:
        self._path = path
        self._ttl = ttl_seconds
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()
        logger.info("ExactMatchCacheAdapter initialised at %s (ttl=%ds)", self._path, self._ttl)

    # ------------------------------------------------------------------
    # BaseCacheAdapter interface
    # ------------------------------------------------------------------

    def get(
        self,
        q: str,
        collection: str,
        filters: str | None,
        min_score: float | None,
    ) -> dict | None:
        """Calcola la chiave internamente e fa lookup SQLite."""
        cache_key = make_cache_key(q, collection, filters, min_score)
        return self._get_by_key(cache_key)

    def set(
        self,
        q: str,
        collection: str,
        filters: str | None,
        min_score: float | None,
        results: dict,
        ttl_seconds: int | None = None,
    ) -> None:
        """Calcola la chiave internamente e salva in SQLite."""
        cache_key = make_cache_key(q, collection, filters, min_score)
        self._set_by_key(cache_key, collection, results, ttl_seconds)

    def invalidate_collection(self, collection: str) -> None:
        try:
            self._conn.execute("DELETE FROM search_cache WHERE collection=?", (collection,))
            self._conn.commit()
            logger.info("ExactMatchCacheAdapter: invalidated all entries for collection %r", collection)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ExactMatchCacheAdapter.invalidate_collection failed: %s", exc)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Protected helpers (usati da NormalizedCacheAdapter e SemanticCacheAdapter
    # per evitare accesso diretto a ._conn)
    # ------------------------------------------------------------------

    def _get_by_key(self, cache_key: str) -> dict | None:
        """Lookup SQLite per chiave pre-calcolata. Evita la entry se scaduta."""
        try:
            now = datetime.now(tz=timezone.utc).isoformat()
            row = self._conn.execute(
                "SELECT results_json, expires_at FROM search_cache WHERE cache_key=?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= now:
                # Scaduta — rimuove
                self._conn.execute("DELETE FROM search_cache WHERE cache_key=?", (cache_key,))
                self._conn.commit()
                return None
            return json.loads(row["results_json"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("ExactMatchCacheAdapter._get_by_key failed: %s", exc)
            return None

    def _set_by_key(
        self,
        cache_key: str,
        collection: str,
        results: dict,
        ttl_seconds: int | None,
    ) -> None:
        """INSERT OR REPLACE per chiave pre-calcolata. Gestisce ttl_seconds=None internamente."""
        try:
            ttl = ttl_seconds if ttl_seconds is not None else self._ttl
            now = datetime.now(tz=timezone.utc)
            expires_at = (now + timedelta(seconds=ttl)).isoformat()
            self._conn.execute(
                "INSERT OR REPLACE INTO search_cache "
                "(cache_key, collection, results_json, created_at, expires_at) VALUES (?,?,?,?,?)",
                (cache_key, collection, json.dumps(results), now.isoformat(), expires_at),
            )
            self._conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ExactMatchCacheAdapter._set_by_key failed: %s", exc)
