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
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth.dependencies import get_current_user
from auth.user_store import UserRecord
from config.settings import _CONFIG_PATH, load_config, settings
from embeddings import build_embedding_adapter
from vector_stores.synonyms import _load_synonyms, _expand_query, _get_omw_synonyms, _ensure_omw_downloaded
from vector_stores.fuzzy import _apply_fuzzy_expansion
# Phase 23: fuzzy vocab populated by Plan 03's QdrantVectorStore.index_records at full-sync time.
# api/search.py reads the module-level dict via .get() at query time.
from vector_stores import qdrant_store as _qdrant_store_mod
# Phase 23: explicit per-route rate limiter (T-23-04-04).
# Re-use the existing _limiter singleton from api.auth so that rate-limit state is shared.
from api.auth import _limiter as limiter

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
    match_mode_override: Annotated[Optional[Literal["and", "or"]], Query(
        alias="match_mode",
        description="Override AND/OR match mode for fts/bm25 modes. Values: 'and' | 'or'. Default uses entity fts.match_mode (default 'and').",
    )] = None,
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

        # Phase 23: OMW synonym expansion (when fts.use_omw=true in entity config)
        if engine == "qdrant" and getattr(getattr(cfg.vector_store, "fts", None), "use_omw", False):
            lang = getattr(cfg.vector_store.fts, "language", "en")
            try:
                _ensure_omw_downloaded(lang)
                omw_extras: list[str] = []
                for token in embed_q.split():
                    omw_extras.extend(_get_omw_synonyms(token, lang))
                # Append OMW lemmas not already in the expanded query
                existing_tokens = set(expand_q.lower().split())
                omw_new = [w for w in omw_extras if w not in existing_tokens]
                if omw_new:
                    expand_q = expand_q + " " + " ".join(omw_new)
                    logger.info("OMW expansion: added %d lemmas", len(omw_new))
            except Exception as exc:  # noqa: BLE001
                logger.warning("OMW expansion failed: %s", exc)

        # Phase 23: fuzzy expansion (Qdrant only).
        # Vocab is populated by Plan 03's QdrantVectorStore.index_records via _fts_text scroll
        # at full-sync time. When a collection has not yet been synced, vocab is empty and
        # _apply_fuzzy_expansion returns the input query unchanged (graceful no-op).
        #
        # AND-mode guard: fuzzy variants are OR-semantic by nature (find this term OR a close
        # variant). Passing "mimmuzzo mimmuzza" to MatchText(AND) would require BOTH tokens to
        # be present in a document — the opposite of what fuzzy search intends.
        # Safe cases:
        #   • single-term queries: AND/OR distinction is moot for one token; expansion + OR
        #     pre-filter is always correct.
        #   • OR mode: flat union of original + variants is exactly the right semantics.
        # Unsafe case:
        #   • multi-term AND: "(A OR A') AND (B OR B')" cannot be expressed as a flat string
        #     with MatchText/MatchTextAny — skip expansion, preserve the user's strict AND.
        _fuzzy_expanded = False
        if engine == "qdrant":
            fuzzy_vocab: frozenset[str] = _qdrant_store_mod._fuzzy_vocab.get(
                effective_collection or "", frozenset()
            )
            lang_for_fuzzy = (
                getattr(cfg.vector_store.fts, "language", "en")
                if hasattr(cfg.vector_store, "fts") else "en"
            )
            _resolved_match_mode = match_mode_override or getattr(
                getattr(cfg.vector_store, "fts", None), "match_mode", "and"
            )
            _pre_expansion_term_count = len(expand_q.split())
            # fts/bm25: Snowball payload index already handles morphological variants via
            # stemming — fuzzy expansion on top produces mismatches (e.g. "mimmuzzo" →
            # "mimmuzzo mimmuzzi" misses "mimmo" which Snowball maps to the same stem).
            _fuzzy_eligible = effective_mode not in ("fts", "bm25")
            if _fuzzy_eligible and (_pre_expansion_term_count == 1 or _resolved_match_mode == "or"):
                expand_q_fuzzy = _apply_fuzzy_expansion(expand_q, fuzzy_vocab, lang=lang_for_fuzzy)
                if expand_q_fuzzy != expand_q:
                    logger.info("Fuzzy expansion: %r → %r", expand_q, expand_q_fuzzy)
                    expand_q = expand_q_fuzzy
                    _fuzzy_expanded = True

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
            # When fuzzy expansion added variants, force OR so the pre-filter admits any
            # variant. Without this, MatchText(AND) would require ALL expanded tokens to
            # coexist in a single document, defeating fuzzy recall entirely.
            match_mode_override="or" if _fuzzy_expanded else match_mode_override,
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


@router.get("/search/suggest")
@limiter.limit("60/minute")
async def search_suggest(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    collection: Annotated[str, Query(...)],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
    _user: UserRecord = Depends(get_current_user),
) -> list[str]:
    """Return autocomplete suggestions from Qdrant scroll on _fts_text (Qdrant-only).

    Path-traversal guard: collection validated via _COLLECTION_RE (T-11-01, T-23-04-02).
    Auth: JWT required (T-23-04-03).
    Rate limit: explicit @limiter.limit("60/minute") (T-23-04-04, W2).
    Weaviate path: returns [] gracefully (no 500).
    Error path: Qdrant error returns [] gracefully (no 500).
    """
    # Path-traversal guard (T-11-01, T-23-04-02) — MUST be first, before engine check
    if not _COLLECTION_RE.match(collection):
        raise HTTPException(status_code=422, detail="Invalid collection name")

    # Qdrant-only — Weaviate path returns empty list gracefully
    if os.getenv("VECTOR_STORE_ENGINE", "weaviate") != "qdrant":
        return []

    config_path = _CONFIG_ROOT / collection / "config.yaml"
    if not config_path.exists():
        return []

    try:
        cfg = load_config(config_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("suggest config load failed for %r: %s", collection, exc)
        return []

    # First text_field used for suggestion values
    # Pitfall 6: text_fields is dict[str, float] — use next(iter(...)) not [0]
    text_fields = cfg.vector_store.text_fields
    text_field = next(iter(text_fields), None) if text_fields else None
    if not text_field:
        return []

    try:
        from qdrant_client import models as qmodels
        vector_store = request.app.state.vector_store
        qdrant_filter = qmodels.Filter(
            must=[qmodels.FieldCondition(
                key="_fts_text",
                match=qmodels.MatchText(text=q),
            )]
        )
        points, _ = vector_store._client.scroll(
            collection_name=cfg.vector_store.collection,
            scroll_filter=qdrant_filter,
            limit=50,
            with_payload=[text_field],
        )
        seen: set[str] = set()
        suggestions: list[str] = []
        for p in points:
            val = str((p.payload or {}).get(text_field, "")).strip()
            if val and val not in seen:
                seen.add(val)
                suggestions.append(val)
            if len(suggestions) >= limit:
                break
        return suggestions
    except Exception as exc:  # noqa: BLE001
        logger.warning("suggest error: %s", exc)
        return []
