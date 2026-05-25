"""Cache adapters package — factory e classi base.

Uso:
    from sync.cache_adapters import build_cache_adapter, BaseCacheAdapter, ExactMatchCacheAdapter

    adapter = build_cache_adapter(settings)
"""
from __future__ import annotations

import logging
from pathlib import Path

from sync.cache_adapters.base import BaseCacheAdapter
from sync.cache_adapters.exact import ExactMatchCacheAdapter

logger = logging.getLogger(__name__)


def build_cache_adapter(settings) -> BaseCacheAdapter:
    """Factory: restituisce il cache adapter corretto per settings.api.cache_mode.

    Supporta: "exact" (default).
    "normalized" e "semantic" sono gestiti nei piani successivi (Phase 13.1 Wave 2/3).
    Fallback a ExactMatchCacheAdapter + logger.warning per modalità non supportate.
    """
    mode = getattr(settings.api, "cache_mode", "exact")
    path = Path("/app/.sync/search_history.db")
    ttl = settings.api.cache_ttl_seconds

    if mode == "exact":
        return ExactMatchCacheAdapter(path, ttl_seconds=ttl)

    # normalized e semantic gestiti nei piani successivi
    logger.warning("cache_mode %r non supportato — fallback a exact", mode)
    return ExactMatchCacheAdapter(path, ttl_seconds=ttl)


__all__ = ["BaseCacheAdapter", "ExactMatchCacheAdapter", "build_cache_adapter"]
