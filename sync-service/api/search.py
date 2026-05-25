"""Search API router — GET /search, GET /search/suggestions.

Uses Weaviate hybrid search (BM25 + vector) so that both keyword queries
(names, codes, exact strings) and semantic queries (concepts, natural language)
work from a single search bar with no user configuration required.

When an embedding adapter is configured (e.g. Ollama), the query vector is
computed client-side and passed to hybrid(). When using weaviate_builtin,
hybrid() lets Weaviate vectorize internally.

Per CONTEXT.md decisions:
- D-02: include _score in every result (replaces _distance for hybrid)
- D-03: allowed fields = text_fields ∪ metadata_fields (not output_fields)
- D-04: default fields when ?fields absent = api.output_fields
- D-05: limit must be 1..max_limit; default = default_limit
- D-08/D-09: exact-match cache keyed by SHA256(q|collection|filters|min_score)
- D-15: GET /search/suggestions for prefix-matched autocomplete from user history
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

import weaviate.classes.query as _wvc_query

from auth.dependencies import get_current_user
from auth.user_store import UserRecord
from config.settings import _CONFIG_PATH, load_config, settings
from embeddings import build_embedding_adapter
from sync.cache_store import make_cache_key
from weaviate_store.client import get_client

_CONFIG_ROOT = _CONFIG_PATH.parent  # configuration/ dir (container) or project_root/configuration/ (host)

# Path-traversal guard: same pattern as api/graph.py _COLLECTION_RE (T-11-01).
_COLLECTION_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/search")
async def search(
    request: Request,
    q: str = Query(..., min_length=1, description="Testo della query semantica"),
    limit: Optional[int] = Query(default=None, description="Numero massimo di risultati"),
    fields: Optional[str] = Query(
        default=None,
        description="Campi da restituire, separati da virgola. Se omesso usa api.output_fields.",
    ),
    filter: Optional[str] = Query(
        default=None,
        description="Filtri strutturati: 'Campo:Valore[,Campo2:Valore2]'",
    ),
    min_score: Optional[float] = Query(
        default=None,
        ge=0.0,
        le=1.0,
        description="Score minimo (0.0–1.0). Esclude risultati con _score inferiore.",
    ),
    collection: Optional[str] = Query(
        default=None,
        description="Nome della collection Weaviate (es. 'Collaboratori'). Se omesso usa config.yaml globale.",
    ),
    _user: UserRecord = Depends(get_current_user),
) -> dict:
    """Ricerca ibrida (BM25 + semantica). Ritorna {query, results:[{...props, _score}, ...], cached}."""
    # --- Per-entity config resolution ----------------------------------------
    # If ?collection= is provided, validate name then load the per-entity config.
    # Otherwise fall back to the global settings singleton (no regression).
    if collection is not None:
        # Path-traversal guard: reject names with '/', '..', spaces, etc. (T-11-01).
        if not _COLLECTION_RE.match(collection):
            raise HTTPException(status_code=422, detail="Invalid collection name")
        config_path = _CONFIG_ROOT / collection / "config.yaml"
        if not config_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"No config found for collection '{collection}'. Upload and confirm the file first.",
            )
        cfg = load_config(config_path)
    else:
        cfg = settings

    # --- Effective collection name (used for cache keying and history logging) --
    effective_collection = collection if collection is not None else cfg.weaviate.collection

    # --- Cache + history references (resolved once, used on both hit and miss paths) ---
    cache_store = getattr(request.app.state, "cache_store", None)
    history_store = getattr(request.app.state, "history_store", None)
    _cache_key = make_cache_key(q, effective_collection, filter, min_score)

    if cache_store is not None:
        try:
            cached = cache_store.get(_cache_key)
            if cached is not None:
                cached["cached"] = True
                # --- History log on cache-HIT path (SC-13-01) ----------------------
                if history_store is not None:
                    try:
                        history_store.log(
                            user_id=_user.username,
                            query=q,
                            collection=effective_collection,
                            filters=filter or "",
                            min_score=min_score,
                            result_count=len(cached.get("results", [])),
                            timestamp=datetime.now(tz=timezone.utc).isoformat(),
                        )
                    except Exception as _hist_exc:  # noqa: BLE001
                        logger.warning("history log error (cache hit): %s", _hist_exc)
                return cached
        except Exception as _cache_exc:  # noqa: BLE001
            logger.warning("cache lookup error: %s", _cache_exc)

    # --- Limit validation --------------------------------------------------
    effective_limit = limit if limit is not None else cfg.api.default_limit
    if not (1 <= effective_limit <= cfg.api.max_limit):
        raise HTTPException(
            status_code=422,
            detail=f"limit must be between 1 and {cfg.api.max_limit}",
        )

    # --- Field projection --------------------------------------------------
    allowed = set(cfg.weaviate.text_fields) | set(cfg.weaviate.metadata_fields)
    if fields:
        requested = [f.strip() for f in fields.split(",") if f.strip()]
        invalid = [f for f in requested if f not in allowed]
        if invalid:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid fields: {invalid}. Allowed: {sorted(allowed)}",
            )
        return_props = requested
    else:
        # id_field is used for UUID generation only and is never stored as a Weaviate property,
        # so filter output_fields to the intersection with text_fields ∪ metadata_fields.
        return_props = [f for f in cfg.api.output_fields if f in allowed] or list(allowed)

    # --- Filter parsing (D-01 through D-09) ------------------------------------
    weaviate_filter = None
    if filter is not None:
        filterable_fields = set(cfg.weaviate.metadata_fields)
        pairs = [p.strip() for p in filter.split(",") if p.strip()]
        parsed_filters = []
        for pair in pairs:
            if ":" not in pair:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid filter format. Expected 'Campo:Valore[,Campo2:Valore2]'",
                )
            campo, valore = pair.split(":", 1)  # D-02: split on first colon only
            campo = campo.strip()
            valore = valore.strip()
            if not campo or not valore:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid filter format. Expected 'Campo:Valore[,Campo2:Valore2]'",
                )
            if campo not in filterable_fields:
                raise HTTPException(
                    status_code=422,
                    detail=f"Field '{campo}' is not in metadata_fields. Filterable fields: {sorted(filterable_fields)}",
                )
            # Weaviate lowercases the first char of every property name at schema creation time.
            weaviate_campo = campo[0].lower() + campo[1:] if campo else campo
            parsed_filters.append(_wvc_query.Filter.by_property(weaviate_campo).like(valore))
        if parsed_filters:
            weaviate_filter = parsed_filters[0]
            for f in parsed_filters[1:]:
                weaviate_filter = weaviate_filter & f  # D-08: AND logic

    # --- Weaviate hybrid query (BM25 + vector) --------------------------------
    # alpha=0.5 balances keyword and semantic equally. Keyword component (BM25)
    # handles names, codes, exact strings; semantic component handles concepts
    # and natural language. Both work from the same search bar with no user config.
    # Per-entity collections may use a different embedding model than the global config.
    # Build the adapter from the resolved cfg so query dims match the indexed vectors.
    if collection is not None:
        embedding_adapter = build_embedding_adapter(cfg.embedding)
    else:
        embedding_adapter = getattr(request.app.state, "embedding_adapter", None)
    _t0 = time.perf_counter()
    try:
        weaviate_col = get_client().collections.get(cfg.weaviate.collection)
        if embedding_adapter is not None:
            # Client-side embedding: compute vector and pass to hybrid()
            query_vectors = embedding_adapter.embed([q])
            results = weaviate_col.query.hybrid(
                query=q,
                vector=query_vectors[0],
                alpha=0.5,
                limit=effective_limit,
                return_properties=return_props,
                return_metadata=_wvc_query.MetadataQuery(score=True),
                filters=weaviate_filter,
            )
        else:
            # Server-side vectorization: hybrid() lets Weaviate vectorize internally
            results = weaviate_col.query.hybrid(
                query=q,
                alpha=0.5,
                limit=effective_limit,
                return_properties=return_props,
                return_metadata=_wvc_query.MetadataQuery(score=True),
                filters=weaviate_filter,
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("search failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail="Search backend unavailable")

    hits = [
        {**obj.properties, "_score": obj.metadata.score}
        for obj in results.objects
        if min_score is None or obj.metadata.score >= min_score
    ]
    took_ms = int((time.perf_counter() - _t0) * 1000)
    response_body = {"query": q, "took_ms": took_ms, "results": hits, "cached": False}

    # --- Cache store (SC-13-05) ------------------------------------------------
    if cache_store is not None:
        try:
            ttl = getattr(cfg.api, "cache_ttl_seconds", 300)
            cache_store.set(_cache_key, effective_collection, response_body, ttl_seconds=ttl)
        except Exception as _cs_exc:  # noqa: BLE001
            logger.warning("cache store error: %s", _cs_exc)

    # --- History log on cache-MISS path (SC-13-01) -----------------------------
    if history_store is not None:
        try:
            history_store.log(
                user_id=_user.username,
                query=q,
                collection=effective_collection,
                filters=filter or "",
                min_score=min_score,
                result_count=len(hits),
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
            )
        except Exception as _hist_exc:  # noqa: BLE001
            logger.warning("history log error (cache miss): %s", _hist_exc)

    return response_body


@router.get("/search/suggestions")
async def search_suggestions(
    request: Request,
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
    _user: UserRecord = Depends(get_current_user),
) -> list[str]:
    """Return autocomplete suggestions from the user's own history (D-15, SC-13-04).

    Prefix-matches the user's own past queries — never exposes other users' queries.
    """
    history_store = getattr(request.app.state, "history_store", None)
    if history_store is None:
        return []
    try:
        return history_store.get_suggestions(_user.username, q, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("suggestions error: %s", exc)
        return []
