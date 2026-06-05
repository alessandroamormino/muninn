"""Unit tests for vector_stores/base.py — VS-01 and VS-08.

All tests use MagicMock. No live containers required.
"""
from __future__ import annotations

import inspect
import re
import uuid
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# VS-01: BaseVectorStore ABC structure
# ---------------------------------------------------------------------------

def test_abc_cannot_instantiate():
    """Instantiating BaseVectorStore directly raises TypeError."""
    from vector_stores.base import BaseVectorStore
    with pytest.raises(TypeError):
        BaseVectorStore()  # type: ignore[abstract]


def test_abc_has_10_methods():
    """BaseVectorStore has exactly 10 abstract methods."""
    from vector_stores.base import BaseVectorStore
    abstract_methods = {
        name
        for name, val in inspect.getmembers(BaseVectorStore)
        if getattr(val, "__isabstractmethod__", False)
    }
    expected = {
        "open", "close", "create_index", "drop_index", "index_exists",
        "index_records", "search", "count", "get_vectors_for_graph", "is_live",
    }
    assert abstract_methods == expected


def test_concrete_subclass_requires_all_methods():
    """A class implementing only 9/10 methods raises TypeError on instantiation."""
    from vector_stores.base import BaseVectorStore, SearchHit, IndexResult

    class Incomplete(BaseVectorStore):
        def open(self): pass
        def close(self): pass
        def create_index(self, cfg): pass
        def drop_index(self, collection_name): pass
        def index_exists(self, collection_name): pass
        def index_records(self, records, cfg, source_type, embedding_adapter=None,
                          id_field=None, start_from_batch=0, on_batch_done=None): pass
        def search(self, query, query_vector, cfg, filters=None, limit=10, mode="hybrid"): pass
        def count(self, collection_name): pass
        def get_vectors_for_graph(self, collection_name, max_nodes=2000): pass
        # is_live intentionally missing

    with pytest.raises(TypeError):
        Incomplete()


# ---------------------------------------------------------------------------
# SearchHit and IndexResult dataclasses
# ---------------------------------------------------------------------------

def test_search_hit_fields():
    """SearchHit(properties={"a": 1}, score=0.9).score == 0.9."""
    from vector_stores.base import SearchHit
    hit = SearchHit(properties={"a": 1}, score=0.9)
    assert hit.score == 0.9
    assert hit.properties == {"a": 1}


def test_index_result_total():
    """IndexResult(inserted=2, updated=1, skipped=0).total == 3."""
    from vector_stores.base import IndexResult
    r = IndexResult(inserted=2, updated=1, skipped=0)
    assert r.total == 3


# ---------------------------------------------------------------------------
# compute_record_uuid
# ---------------------------------------------------------------------------

def test_compute_record_uuid_deterministic():
    """compute_record_uuid('csv', '42') == compute_record_uuid('csv', '42')."""
    from vector_stores.base import compute_record_uuid
    a = compute_record_uuid("csv", "42")
    b = compute_record_uuid("csv", "42")
    assert a == b


def test_compute_record_uuid_returns_str_for_qdrant():
    """str(compute_record_uuid('csv', '42')) matches UUID regex."""
    from vector_stores.base import compute_record_uuid
    result = compute_record_uuid("csv", "42")
    uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    assert uuid_pattern.match(str(result))


# ---------------------------------------------------------------------------
# VS-08: validate_search_mode_compatibility
# ---------------------------------------------------------------------------

def _make_cfg(collection: str, search_mode: str) -> MagicMock:
    """Build a minimal mock AppConfig with weaviate.collection and weaviate.search_mode."""
    cfg = MagicMock()
    cfg.vector_store.collection = collection
    cfg.vector_store.search_mode = search_mode
    return cfg


def test_fail_fast_weaviate_with_fts():
    """validate_search_mode_compatibility('weaviate', [fts_cfg]) raises RuntimeError."""
    from vector_stores.base import validate_search_mode_compatibility
    cfg_fts = _make_cfg("MyEntity", "fts")
    with pytest.raises(RuntimeError) as exc_info:
        validate_search_mode_compatibility("weaviate", [cfg_fts])
    msg = str(exc_info.value)
    assert "fts" in msg
    assert "weaviate" in msg.lower()


def test_fail_fast_weaviate_allowed_modes():
    """validate_search_mode_compatibility('weaviate', [hybrid, vector, bm25]) does not raise."""
    from vector_stores.base import validate_search_mode_compatibility
    cfg_hybrid = _make_cfg("EntityA", "hybrid")
    cfg_vector = _make_cfg("EntityB", "vector")
    cfg_bm25 = _make_cfg("EntityC", "bm25")
    # Should not raise
    validate_search_mode_compatibility("weaviate", [cfg_hybrid, cfg_vector, cfg_bm25])


def test_fail_fast_qdrant_allows_fts():
    """validate_search_mode_compatibility('qdrant', [fts_cfg]) does not raise."""
    from vector_stores.base import validate_search_mode_compatibility
    cfg_fts = _make_cfg("MyEntity", "fts")
    # Should not raise
    validate_search_mode_compatibility("qdrant", [cfg_fts])
