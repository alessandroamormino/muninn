"""Weaviate collection schema initializer.

Creates the collection defined in config.yaml on first run. Idempotent: when the
collection already exists, this is a no-op (drop+recreate for model-version
mismatches is handled in Plan 03-04, NOT here).
"""
from __future__ import annotations

import logging

import weaviate.classes.config as _wvc

from config.settings import VectorStoreConfig

logger = logging.getLogger(__name__)

# Heuristic field-name → Weaviate DataType mapping for metadata_fields (POC).
_NUMBER_FIELDS = {"price", "cost", "amount", "qty", "quantity", "score", "weight", "value", "popularity"}
_NUMBER_SUFFIXES = {"_average", "_count", "_rate", "_score", "_ratio", "_num", "_total", "_amount"}
_BOOL_FIELDS = {"active", "enabled", "published", "deleted"}
# Weaviate v4 reserves these names and forbids them as property names.
_WEAVIATE_RESERVED_PROPS = {"id", "vector"}


def _infer_metadata_datatype(field_name: str):
    name = field_name.lower()
    if name.endswith("_at") or name in {"created", "updated"}:
        return _wvc.DataType.DATE
    if name == "id" or name.endswith("_id"):
        return _wvc.DataType.TEXT
    if name in _NUMBER_FIELDS or any(name.endswith(s) for s in _NUMBER_SUFFIXES):
        return _wvc.DataType.NUMBER
    if name in _BOOL_FIELDS:
        return _wvc.DataType.BOOL
    return _wvc.DataType.TEXT


def _build_properties(weaviate_cfg: VectorStoreConfig) -> list:
    """Build the Property list: text_fields vectorized, metadata_fields not vectorized.

    If a field appears in BOTH text_fields and metadata_fields, the text_fields
    entry wins (the text version is the vectorized one); the metadata duplicate
    is skipped to avoid a duplicate-property error from Weaviate.
    """
    properties = []
    seen: set[str] = set()

    for field in weaviate_cfg.text_fields:
        if field in seen:
            continue
        if field in _WEAVIATE_RESERVED_PROPS:
            logger.warning("text_field %r is a Weaviate reserved name; skipping.", field)
            continue
        properties.append(
            _wvc.Property(
                name=field,
                data_type=_wvc.DataType.TEXT,
                skip_vectorization=False,
            )
        )
        seen.add(field)

    for field in weaviate_cfg.metadata_fields:
        if field in seen:
            logger.info("metadata_field %r already declared as text_field; skipping duplicate.", field)
            continue
        if field in _WEAVIATE_RESERVED_PROPS:
            logger.warning("metadata_field %r is a Weaviate reserved name; skipping.", field)
            continue
        properties.append(
            _wvc.Property(
                name=field,
                data_type=_infer_metadata_datatype(field),
                skip_vectorization=True,
            )
        )
        seen.add(field)

    return properties


def _build_quantizer_config(weaviate_cfg: VectorStoreConfig, dims: int | None = None):
    """Return the HNSW index config (with optional quantizer), or None if no overrides.

    Quantization options:
      pq  = Product Quantization  -- ~32x RAM reduction, ~2-5% quality loss   (>100K records)
      bq  = Binary Quantization   -- ~128x RAM reduction, ~10-15% quality loss (RAM critical)
      sq  = Scalar Quantization   -- ~4x RAM reduction, ~1-2% quality loss    (10K-100K records, Phase 13.2)
      none = no compression (default)

    HNSW tuning (Phase 13.2):
      - cfg.hnsw.ef             -- MUTABLE post-creation via Reconfigure.VectorIndex.hnsw(ef=N)
      - cfg.hnsw.max_connections -- IMMUTABLE after creation; requires full re-index to change

    Only non-None HNSW kwargs are passed to Configure.VectorIndex.hnsw() to avoid
    overriding Weaviate server defaults with explicit nulls (see RESEARCH.md Pitfall 3).

    NOTE: quantization cannot be added to an existing collection -- a full re-index is
    required to apply it retroactively.
    """
    q = getattr(weaviate_cfg, "quantization", "none")
    hnsw_cfg = getattr(weaviate_cfg, "hnsw", None)

    # Build HNSW kwargs dict -- only include non-None values (Pitfall 3).
    hnsw_kwargs: dict = {}
    if hnsw_cfg is not None:
        if getattr(hnsw_cfg, "ef", None) is not None:
            hnsw_kwargs["ef"] = hnsw_cfg.ef
        if getattr(hnsw_cfg, "max_connections", None) is not None:
            # max_connections is IMMUTABLE after creation -- set only at create time.
            hnsw_kwargs["max_connections"] = hnsw_cfg.max_connections

    if q == "pq":
        effective_dims = dims or 1024
        segments = max(1, effective_dims // 8)
        logger.info("PQ quantization: segments=%d (dims=%d), training_limit=100000", segments, effective_dims)
        return _wvc.Configure.VectorIndex.hnsw(
            quantizer=_wvc.Configure.VectorIndex.Quantizer.pq(
                segments=segments,
                training_limit=100_000,
            ),
            **hnsw_kwargs,
        )
    if q == "bq":
        logger.info("BQ quantization enabled.")
        return _wvc.Configure.VectorIndex.hnsw(
            quantizer=_wvc.Configure.VectorIndex.Quantizer.bq(),
            **hnsw_kwargs,
        )
    if q == "sq":
        # ~4x RAM reduction, ~1-2% quality loss -- recommended for 10K-100K records.
        logger.info("SQ quantization enabled (training_limit=100000).")
        return _wvc.Configure.VectorIndex.hnsw(
            quantizer=_wvc.Configure.VectorIndex.Quantizer.sq(
                training_limit=100_000,
            ),
            **hnsw_kwargs,
        )
    # quantization == "none": return hnsw config only if HNSW overrides were requested.
    if hnsw_kwargs:
        return _wvc.Configure.VectorIndex.hnsw(**hnsw_kwargs)
    return None  # no quantization, no HNSW override -- Weaviate default.


def create_collection_if_missing(
    client, weaviate_cfg: VectorStoreConfig, embedding_type: str = "weaviate_builtin",
    embedding_dims: int | None = None,
) -> bool:
    """Create the Weaviate collection if it does not already exist.

    Returns True if a new collection was created, False if it already existed.
    Idempotent: safe to call on every startup.

    When embedding_type is "ollama" (or any non-builtin adapter), Weaviate is
    configured with no vectorizer — vectors are passed explicitly on each insert.

    When weaviate_cfg.quantization is "pq", "bq", or "sq", the HNSW index is created
    with the corresponding quantizer to reduce RAM usage. Optional weaviate_cfg.hnsw
    settings (ef, max_connections) are applied at creation time.
    NOTE: quantization cannot be added to an existing collection -- a full re-index
    is required. max_connections is IMMUTABLE after creation; ef is mutable via
    collection.config.update(vector_index_config=Reconfigure.VectorIndex.hnsw(ef=N)).
    """
    name = weaviate_cfg.collection
    if client.collections.exists(name):
        logger.info("Weaviate collection %r already exists; skipping create.", name)
        return False

    properties = _build_properties(weaviate_cfg)

    if embedding_type == "weaviate_builtin":
        vectorizer_config = _wvc.Configure.Vectorizer.text2vec_transformers()
    else:
        vectorizer_config = _wvc.Configure.Vectorizer.none()

    vector_index_config = _build_quantizer_config(weaviate_cfg, dims=embedding_dims)

    q_label = getattr(weaviate_cfg, "quantization", "none")
    logger.info(
        "Creating Weaviate collection %r with %d properties (vectorizer=%s, quantization=%s, "
        "text_fields=%s, metadata_fields=%s).",
        name, len(properties), embedding_type, q_label,
        weaviate_cfg.text_fields, weaviate_cfg.metadata_fields,
    )

    create_kwargs: dict = dict(
        name=name,
        vectorizer_config=vectorizer_config,
        properties=properties,
    )
    if vector_index_config is not None:
        create_kwargs["vector_index_config"] = vector_index_config

    client.collections.create(**create_kwargs)
    return True
