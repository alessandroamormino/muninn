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


def create_collection_if_missing(
    client, weaviate_cfg: WeaviateConfig, embedding_type: str = "weaviate_builtin"
) -> bool:
    """Create the Weaviate collection if it does not already exist.

    Returns True if a new collection was created, False if it already existed.
    Idempotent: safe to call on every startup.

    When embedding_type is "ollama" (or any non-builtin adapter), Weaviate is
    configured with no vectorizer — vectors are passed explicitly on each insert.
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

    logger.info(
        "Creating Weaviate collection %r with %d properties (vectorizer=%s, "
        "text_fields=%s, metadata_fields=%s).",
        name, len(properties), embedding_type, weaviate_cfg.text_fields, weaviate_cfg.metadata_fields,
    )
    client.collections.create(
        name=name,
        vectorizer_config=vectorizer_config,
        properties=properties,
    )
    return True
