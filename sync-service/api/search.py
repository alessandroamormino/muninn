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

Negation-aware search:
- Detects Italian negation tokens (non, no, nessuno, senza, …) in the query
- Strips negation from the query used for embedding (better semantic vector)
- Post-filters results: excludes any record where a metadata field value
  contains the entity following the negation token (case-insensitive substring)
- Fetches 3× limit from Weaviate to compensate for post-filter exclusions
- Exposes negation_query and negation_entities in the response for transparency
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth.dependencies import get_current_user
from auth.user_store import UserRecord
from config.settings import _CONFIG_PATH, load_config, settings
from embeddings import build_embedding_adapter
from vector_stores.synonyms import _load_synonyms, _expand_query

_CONFIG_ROOT = _CONFIG_PATH.parent  # configuration/ dir (container) or project_root/configuration/ (host)

# Path-traversal guard: same pattern as api/graph.py _COLLECTION_RE (T-11-01).
_COLLECTION_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Italian negation tokens — same set used by NormalizedCacheAdapter and SemanticCacheAdapter.
_NEGATION_TOKENS = frozenset({
    "non", "no", "nessun", "nessuno", "nessuna", "senza", "mai",
    "né", "nemmeno", "neanche", "niente", "nulla",
})

# Italian prepositions/articles to skip when extracting the negated entity.
_IT_STOPWORDS = frozenset({
    "in", "di", "a", "da", "su", "con", "per", "tra", "fra",
    "il", "la", "lo", "i", "gli", "le", "un", "una", "uno",
    "del", "della", "dello", "dei", "degli", "delle",
    "al", "alla", "allo", "ai", "agli", "alle",
    "dal", "dalla", "dallo", "dai", "dagli", "dalle",
    "nel", "nella", "nello", "nei", "negli", "nelle",
    "sul", "sulla", "sullo", "sui", "sugli", "sulle",
    "che", "chi", "come", "dove", "quando", "quale", "quali",
    "è", "e", "o", "ma", "se", "anche", "solo",
})

logger = logging.getLogger(__name__)
router = APIRouter()


def _parse_negation(q: str) -> tuple[str, list[str]]:
    """Detect negation tokens and extract entity candidates that follow them.

    Strategy:
    - Tokenize on whitespace, strip trailing punctuation per token.
    - When a negation token is found, look ahead for the entity being negated.
      Entity extraction priority:
        1. First meaningful word AFTER a preposition/locative ("in", "di", "a", …)
           e.g. "non lavora in apping" → entity = "apping"
        2. Fallback: last meaningful word in the next 3 tokens
           e.g. "senza java" → entity = "java"
    - The negation token itself is removed from the clean query (better embedding).
    - Entities are NOT removed from clean_q — the semantic vector needs them.

    Returns:
        clean_q: query with negation tokens stripped (used for vector embedding).
        neg_entities: lowercase entity strings to exclude via post-filter.

    Examples:
        "chi non lavora in apping"   → ("chi lavora in apping",   ["apping"])
        "chi NON ha lavorato in apping?" → ("chi ha lavorato in apping?", ["apping"])
        "sviluppatori senza java"    → ("sviluppatori java",       ["java"])
        "chi lavora in apping"       → ("chi lavora in apping",   [])  # no negation
    """
    # Italian prepositions that typically precede the negated entity
    _LOCATIVE_PREPS = frozenset({"in", "di", "a", "da", "su", "per", "con", "tra", "fra"})

    tokens = q.split()
    clean_tokens: list[str] = []
    neg_entities: list[str] = []

    i = 0
    while i < len(tokens):
        tok_clean = tokens[i].lower().rstrip("?!,.:;")
        if tok_clean in _NEGATION_TOKENS:
            # Look ahead up to 5 tokens: try to find "preposition + entity" first.
            window = tokens[i + 1: i + 6]
            found_after_prep: str | None = None
            fallback_last: str | None = None
            prev_was_prep = False
            for wt in window:
                wt_clean = wt.lower().rstrip("?!,.:;")
                if wt_clean in _NEGATION_TOKENS:
                    break  # second negation token — stop here
                if wt_clean in _LOCATIVE_PREPS:
                    prev_was_prep = True
                    continue
                if wt_clean not in _IT_STOPWORDS:
                    if prev_was_prep and found_after_prep is None:
                        found_after_prep = wt_clean  # highest priority
                    fallback_last = wt_clean
                prev_was_prep = False

            entity = found_after_prep or fallback_last
            if entity:
                neg_entities.append(entity)

            # Skip only the negation token; remaining tokens stay in clean_tokens.
            i += 1
            continue

        clean_tokens.append(tokens[i])
        i += 1

    clean_q = " ".join(clean_tokens).strip() or q  # never return empty string
    return clean_q, neg_entities


def _apply_negation_filter(props: dict, neg_entities: list[str]) -> bool:
    """Return True if the record should be EXCLUDED (matches a negated entity).

    Checks every property value in props for a case-insensitive substring match
    against any negated entity. A single match is enough to exclude the record.
    """
    for val in props.values():
        if val is None:
            continue
        val_lower = str(val).lower()
        if any(entity in val_lower for entity in neg_entities):
            return True
    return False


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
    search_mode_override: Optional[str] = Query(
        default=None,
        description="Override search_mode for this request (e.g. 'fts', 'bm25', 'vector', 'hybrid'). If omitted uses entity config.",
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
    effective_collection = collection if collection is not None else cfg.vector_store.collection

    # --- Cache + history references (resolved once, used on both hit and miss paths) ---
    cache_store = getattr(request.app.state, "cache_store", None)
    history_store = getattr(request.app.state, "history_store", None)

    if cache_store is not None:
        try:
            cached = cache_store.get(q, effective_collection, filter, min_score)
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
    allowed = set(cfg.vector_store.text_fields) | set(cfg.vector_store.metadata_fields)
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
    # Parse campo:valore pairs into engine-agnostic list[tuple[str, str]].
    # Engine-specific transforms (e.g. Weaviate first-char lowercase) are applied
    # inside the vector store implementation — search.py stays engine-agnostic.
    parsed_filter_pairs: list[tuple[str, str]] = []
    if filter is not None:
        filterable_fields = set(cfg.vector_store.metadata_fields)
        pairs = [p.strip() for p in filter.split(",") if p.strip()]
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
            parsed_filter_pairs.append((campo, valore))

    # --- Negation detection ---------------------------------------------------
    # Detect Italian negation tokens in the query. When found:
    # - embed_q = query without negation tokens (better semantic vector)
    # - neg_entities = candidate strings to exclude via post-filter
    # - fetch_limit = 3× effective_limit to compensate for post-filter exclusions
    neg_entities: list[str] = []
    embed_q = q
    fetch_limit = effective_limit
    if any(t.lower().rstrip("?!,.:;") in _NEGATION_TOKENS for t in q.split()):
        embed_q, neg_entities = _parse_negation(q)
        if neg_entities:
            fetch_limit = min(effective_limit * 3, cfg.api.max_limit)
            logger.info(
                "Negation detected: embed_q=%r, entities=%r, fetch_limit=%d",
                embed_q, neg_entities, fetch_limit,
            )

    # --- Vector store search (engine-agnostic via BaseVectorStore.search()) ----
    # Determine effective_mode first so embedding can be skipped for fts/bm25.
    effective_mode = (
        search_mode_override
        if search_mode_override is not None
        else getattr(cfg.vector_store, "search_mode", "hybrid")
    )
    # Per-entity collections may use a different embedding model than the global config.
    # Build the adapter from the resolved cfg so query dims match the indexed vectors.
    # Skip entirely for fts/bm25 modes — no dense vector needed (Ollama not required).
    _needs_embedding = effective_mode in ("hybrid", "vector")
    if not _needs_embedding:
        embedding_adapter = None
    elif collection is not None:
        embedding_adapter = build_embedding_adapter(cfg.embedding)
    else:
        embedding_adapter = getattr(request.app.state, "embedding_adapter", None)
    _t0 = time.perf_counter()
    try:
        vector_store = request.app.state.vector_store
        # Synonym expansion for Qdrant (D-13): applied at query-time to all Qdrant modes.
        # For Weaviate, synonym expansion is not needed — built-in BM25 handles it.
        engine = os.getenv("VECTOR_STORE_ENGINE", "weaviate")
        expand_q = embed_q
        if engine == "qdrant" and effective_collection is not None:
            synonym_groups = _load_synonyms(_CONFIG_ROOT, effective_collection)
            if synonym_groups:
                expand_q = _expand_query(embed_q, synonym_groups)
                if expand_q != embed_q:
                    logger.info(
                        "Synonym expansion: %r → %r (collection=%r)",
                        embed_q, expand_q, effective_collection,
                    )

        if embedding_adapter is not None:
            # Client-side embedding: embed the clean query (negation stripped + synonyms expanded)
            # for a better semantic vector. The original q is still passed as BM25 text.
            query_vectors = embedding_adapter.embed([expand_q])
            query_vector = query_vectors[0]
        else:
            query_vector = None

        # For fts/bm25 + negation: pass neg_entities as must_not_text_terms so the vector
        # store handles exclusion server-side via scroll+must_not (Qdrant). BM25 alone
        # cannot surface records that don't contain the negated entity — they score 0.
        _must_not = neg_entities if (effective_mode in ("fts", "bm25") and neg_entities) else None

        search_hits = vector_store.search(
            query=expand_q,
            query_vector=query_vector,
            cfg=cfg,
            filters=parsed_filter_pairs if parsed_filter_pairs else None,
            limit=fetch_limit,
            mode=effective_mode,
            must_not_text_terms=_must_not,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("search failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail="Search backend unavailable")

    # --- Post-filter: apply negation exclusions and min_score -----------------
    # For fts/bm25: Qdrant already excluded neg_entities server-side via must_not.
    # For hybrid/vector: post-filter handles negation (no must_not passed to store).
    _post_filter_entities = neg_entities if effective_mode not in ("fts", "bm25") else []
    hits = []
    for hit in search_hits:
        if min_score is not None and hit.score < min_score:
            continue
        if _post_filter_entities and _apply_negation_filter(hit.properties, _post_filter_entities):
            continue
        # Apply field projection: only include return_props from the hit properties
        projected = {k: v for k, v in hit.properties.items() if k in return_props} if return_props else dict(hit.properties)
        hits.append({**projected, "_score": hit.score})
        if len(hits) >= effective_limit:
            break

    took_ms = int((time.perf_counter() - _t0) * 1000)
    response_body: dict = {"query": q, "took_ms": took_ms, "results": hits, "cached": False}
    if neg_entities:
        response_body["negation_query"] = embed_q
        response_body["negation_entities"] = neg_entities

    # --- Cache store (SC-13-05) ------------------------------------------------
    if cache_store is not None:
        try:
            ttl = getattr(cfg.api, "cache_ttl_seconds", 300)
            cache_store.set(q, effective_collection, filter, min_score, response_body, ttl_seconds=ttl)
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
