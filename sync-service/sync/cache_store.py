"""Backward-compat re-export per CacheStore.

Tutto il codice vero è in sync/cache_adapters/exact.py (Phase 13.1).
Questo modulo esiste perché api/search.py importava make_cache_key da qui
e main.py (versioni < 13.1) importava CacheStore da qui.
"""
from sync.cache_adapters.exact import ExactMatchCacheAdapter, make_cache_key  # noqa: F401

# Alias backward-compat — non rimuovere: main.py legacy e test esistenti usano CacheStore
CacheStore = ExactMatchCacheAdapter

__all__ = ["CacheStore", "make_cache_key"]
