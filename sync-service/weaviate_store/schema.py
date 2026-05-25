"""Weaviate collection schema initializer.

Creates the collection defined in config.yaml on first run. Idempotent: when the
collection already exists, this is a no-op (drop+recreate for model-version
mismatches is handled in Plan 03-04, NOT here).
"""
from __future__ import annotations

import logging

import weaviate.classes.config as _wvc

from config.settings import WeaviateConfig

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


def _build_properties(weaviate_cfg: WeaviateConfig) -> list:
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


def _build_quantizer_config(weaviate_cfg: WeaviateConfig, dims: int | None = None):
    """Return the quantizer config for the HNSW index, or None if quantization is disabled.

    pq  = Product Quantization  — ~32× RAM reduction, ~2-5% quality loss.
          segments = dims // 8  (Weaviate recommendation: 8 dims per segment).
          training_limit = 100_000 (default; Weaviate trains the codebook on the first N objects).
    bq  = Binary Quantization   — ~128× RAM reduction, ~10-15% quality loss.
    none = no compression.
    """
    q = getattr(weaviate_cfg, "quantization", "none")
    if q == "pq":
        # segments must divide evenly into dims; fall back to 128 if dims unknown.
        effective_dims = dims or 1024
        segments = max(1, effective_dims // 8)
        logger.info("PQ quantization: segments=%d (dims=%d), training_limit=100000", segments, effective_dims)
        return _wvc.Configure.VectorIndex.hnsw(
            quantizer=_wvc.Configure.VectorIndex.Quantizer.pq(
                segments=segments,
                training_limit=100_000,
            )
        )
    if q == "bq":
        logger.info("BQ quantization enabled.")
        return _wvc.Configure.VectorIndex.hnsw(
            quantizer=_wvc.Configure.VectorIndex.Quantizer.bq()
        )
    return None  # no quantization — Weaviate default HNSW


def create_collection_if_missing(
    client, weaviate_cfg: WeaviateConfig, embedding_type: str = "weaviate_builtin",
    embedding_dims: int | None = None,
) -> bool:
    """Create the Weaviate collection if it does not already exist.

    Returns True if a new collection was created, False if it already existed.
    Idempotent: safe to call on every startup.

    When embedding_type is "ollama" (or any non-builtin adapter), Weaviate is
    configured with no vectorizer — vectors are passed explicitly on each insert.

    When weaviate_cfg.quantization is "pq" or "bq", the HNSW index is created
    with the corresponding quantizer to reduce RAM usage significantly.
    NOTE: quantization cannot be added to an existing collection — it must be set
    at creation time. A full re-index is required to apply it retroactively.
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
