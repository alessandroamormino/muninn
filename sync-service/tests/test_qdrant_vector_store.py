"""Unit tests for QdrantVectorStore — VS-03, VS-04, VS-08.

All tests use unittest.mock.MagicMock to mock QdrantClient.
No live containers needed.

VS-03: create_index creates correct collection schema per search_mode
VS-04: search dispatches to correct Qdrant query API per mode
VS-08: fts mode supported (fail-fast only applies to incompatible engine+mode combo)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from qdrant_client import models as qmodels


# ---------------------------------------------------------------------------
# Helper: build minimal AppConfig mock for a given search_mode
# ---------------------------------------------------------------------------

def _make_cfg(search_mode: str = "hybrid", collection: str = "TestCollection",
              text_fields: list | None = None, fts_language: str = "en"):
    cfg = MagicMock()
    cfg.vector_store.collection = collection
    cfg.vector_store.search_mode = search_mode
    cfg.vector_store.text_fields = text_fields or ["name", "description"]
    cfg.vector_store.metadata_fields = ["ruolo"]
    cfg.vector_store.fts.language = fts_language
    cfg.source.id_field = "id"
    return cfg


# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------

def _make_store(url: str = "http://localhost:6333"):
    from vector_stores.qdrant_store import QdrantVectorStore
    return QdrantVectorStore(url)


# ---------------------------------------------------------------------------
# VS-03: create_index schema creation tests
# ---------------------------------------------------------------------------

class TestCreateIndex:
    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_create_index_hybrid(self, MockClient):
        """hybrid mode: dense + sparse + _fts_text payload index."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = False
        store = _make_store()
        store.open()

        store.create_index(_make_cfg("hybrid"))

        # Verify create_collection called with both dense and sparse
        call_kwargs = mock_client.create_collection.call_args[1]
        assert "dense" in call_kwargs["vectors_config"]
        assert "sparse" in call_kwargs["sparse_vectors_config"]
        # _fts_text payload index created for hybrid
        assert mock_client.create_payload_index.called
        fts_call = mock_client.create_payload_index.call_args[1]
        assert fts_call["field_name"] == "_fts_text"

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_create_index_vector(self, MockClient):
        """vector mode: only dense vector, no sparse, no _fts_text index."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = False
        store = _make_store()
        store.open()

        store.create_index(_make_cfg("vector"))

        call_kwargs = mock_client.create_collection.call_args[1]
        assert "dense" in call_kwargs["vectors_config"]
        # No sparse for vector mode
        assert not call_kwargs.get("sparse_vectors_config")
        # No _fts_text payload index
        assert not mock_client.create_payload_index.called

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_create_index_bm25(self, MockClient):
        """bm25 mode: sparse only (no dense), + _fts_text payload index."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = False
        store = _make_store()
        store.open()

        store.create_index(_make_cfg("bm25"))

        call_kwargs = mock_client.create_collection.call_args[1]
        # No dense vectors for bm25
        assert "dense" not in call_kwargs.get("vectors_config", {})
        # Sparse present
        assert "sparse" in call_kwargs["sparse_vectors_config"]
        # _fts_text payload index
        assert mock_client.create_payload_index.called

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_create_index_fts(self, MockClient):
        """fts mode: sparse (for BM25 scoring) + _fts_text payload index; no dense."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = False
        store = _make_store()
        store.open()

        store.create_index(_make_cfg("fts"))

        call_kwargs = mock_client.create_collection.call_args[1]
        # No dense vector in fts mode
        assert "dense" not in call_kwargs.get("vectors_config", {})
        # Sparse for BM25 scoring (PITFALL 1: fts needs sparse for ranked results)
        assert "sparse" in call_kwargs["sparse_vectors_config"]
        # _fts_text index for stemming quality
        assert mock_client.create_payload_index.called

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_create_index_returns_true_when_created(self, MockClient):
        """Returns True when collection did not exist."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = False
        store = _make_store()
        store.open()

        result = store.create_index(_make_cfg("hybrid"))
        assert result is True

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_create_index_returns_false_when_exists(self, MockClient):
        """Returns False when collection already exists (no-op)."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = True
        store = _make_store()
        store.open()

        result = store.create_index(_make_cfg("hybrid"))
        assert result is False
        mock_client.create_collection.assert_not_called()

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_create_index_italian_stemmer(self, MockClient):
        """Italian language config produces SnowballLanguage.ITALIAN in payload index."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = False
        store = _make_store()
        store.open()

        store.create_index(_make_cfg("fts", fts_language="it"))

        fts_call = mock_client.create_payload_index.call_args[1]
        schema = fts_call["field_schema"]
        # Verify stemmer language is ITALIAN
        assert schema.stemmer.language == qmodels.SnowballLanguage.ITALIAN


# ---------------------------------------------------------------------------
# VS-04: search dispatch tests
# ---------------------------------------------------------------------------

def _make_scored_point(payload: dict, score: float):
    p = MagicMock()
    p.payload = payload
    p.score = score
    return p


class TestSearch:
    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_search_hybrid(self, MockClient):
        """hybrid: query_points with RRF prefetch (sparse + dense)."""
        mock_client = MockClient.return_value
        mock_results = MagicMock()
        mock_results.points = []
        mock_client.query_points.return_value = mock_results
        store = _make_store()
        store.open()

        store.search("query text", [0.1, 0.2], _make_cfg("hybrid"), limit=5, mode="hybrid")

        assert mock_client.query_points.called
        call_kwargs = mock_client.query_points.call_args[1]
        # hybrid: uses prefetch + FusionQuery
        assert "prefetch" in call_kwargs
        assert len(call_kwargs["prefetch"]) == 2
        assert isinstance(call_kwargs["query"], qmodels.FusionQuery)
        assert call_kwargs["query"].fusion == qmodels.Fusion.RRF

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_search_vector(self, MockClient):
        """vector: query_points with query=vector, using="dense"."""
        mock_client = MockClient.return_value
        mock_results = MagicMock()
        mock_results.points = []
        mock_client.query_points.return_value = mock_results
        store = _make_store()
        store.open()

        query_vec = [0.1, 0.2, 0.3]
        store.search("query", query_vec, _make_cfg("vector"), limit=5, mode="vector")

        call_kwargs = mock_client.query_points.call_args[1]
        assert call_kwargs.get("using") == "dense"
        assert call_kwargs.get("query") == query_vec
        assert "prefetch" not in call_kwargs

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_search_bm25(self, MockClient):
        """bm25: query_points with Document(text=q, model='Qdrant/bm25'), using='sparse'."""
        mock_client = MockClient.return_value
        mock_results = MagicMock()
        mock_results.points = []
        mock_client.query_points.return_value = mock_results
        store = _make_store()
        store.open()

        store.search("query text", None, _make_cfg("bm25"), limit=5, mode="bm25")

        call_kwargs = mock_client.query_points.call_args[1]
        assert call_kwargs.get("using") == "sparse"
        assert isinstance(call_kwargs.get("query"), qmodels.Document)
        assert call_kwargs["query"].text == "query text"
        assert "Qdrant/bm25" in call_kwargs["query"].model.lower() or "bm25" in call_kwargs["query"].model.lower()

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_search_fts(self, MockClient):
        """fts mode: query_points with Document BM25 sparse — NOT MatchText filter (PITFALL 1)."""
        mock_client = MockClient.return_value
        mock_results = MagicMock()
        mock_results.points = []
        mock_client.query_points.return_value = mock_results
        store = _make_store()
        store.open()

        store.search("query text", None, _make_cfg("fts"), limit=5, mode="fts")

        call_kwargs = mock_client.query_points.call_args[1]
        # CRITICAL: must use sparse BM25, NOT MatchText filter
        assert call_kwargs.get("using") == "sparse"
        assert isinstance(call_kwargs.get("query"), qmodels.Document)
        # Must NOT pass a MatchText filter as the query
        query = call_kwargs.get("query")
        assert not isinstance(query, qmodels.MatchText)

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_search_returns_search_hits(self, MockClient):
        """search() returns list[SearchHit] from ScoredPoint mocks."""
        from vector_stores.base import SearchHit
        mock_client = MockClient.return_value
        mock_results = MagicMock()
        mock_results.points = [
            _make_scored_point({"nome": "Mario"}, 0.9),
            _make_scored_point({"nome": "Luigi"}, 0.7),
        ]
        mock_client.query_points.return_value = mock_results
        store = _make_store()
        store.open()

        hits = store.search("mario", None, _make_cfg("bm25"), limit=5, mode="bm25")

        assert len(hits) == 2
        assert all(isinstance(h, SearchHit) for h in hits)
        assert hits[0].properties == {"nome": "Mario"}
        assert hits[0].score == 0.9

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_search_with_filters(self, MockClient):
        """filters passed to query_points as FieldCondition(key=campo, MatchValue(value))."""
        mock_client = MockClient.return_value
        mock_results = MagicMock()
        mock_results.points = []
        mock_client.query_points.return_value = mock_results
        store = _make_store()
        store.open()

        store.search(
            "query", None, _make_cfg("bm25"),
            filters=[("ruolo", "Sviluppatore")],
            limit=5, mode="bm25",
        )

        call_kwargs = mock_client.query_points.call_args[1]
        qdrant_filter = call_kwargs.get("query_filter")
        assert qdrant_filter is not None
        assert isinstance(qdrant_filter, qmodels.Filter)
        must = qdrant_filter.must
        assert len(must) == 1
        assert must[0].key == "ruolo"
        assert must[0].match.value == "Sviluppatore"


# ---------------------------------------------------------------------------
# VS-08: fail-fast + fts support
# ---------------------------------------------------------------------------

class TestFailFastAndFts:
    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_qdrant_supports_fts_mode(self, MockClient):
        """QdrantVectorStore accepts fts mode without raising."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = False
        store = _make_store()
        store.open()
        # Should not raise
        result = store.create_index(_make_cfg("fts"))
        assert result is True

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_search_unknown_mode_raises(self, MockClient):
        """search() raises ValueError for unknown mode."""
        mock_client = MockClient.return_value
        store = _make_store()
        store.open()
        with pytest.raises(ValueError, match="Unknown search mode"):
            store.search("q", None, _make_cfg("unknown_mode"), mode="unknown_mode")


# ---------------------------------------------------------------------------
# index_records tests
# ---------------------------------------------------------------------------

class TestIndexRecords:
    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_index_records_fts_skips_embedding(self, MockClient):
        """fts mode: embedding_adapter.embed() is NEVER called."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = True
        embedding_adapter = MagicMock()
        store = _make_store()
        store.open()

        records = [{"id": "1", "name": "Mario", "description": "Test"}]
        store.index_records(records, _make_cfg("fts"), "csv", embedding_adapter=embedding_adapter)

        embedding_adapter.embed.assert_not_called()

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_index_records_stores_fts_text(self, MockClient):
        """_fts_text field is included in PointStruct payload for all modes."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = True
        store = _make_store()
        store.open()

        cfg = _make_cfg("fts", text_fields=["name", "description"])
        records = [{"id": "1", "name": "Mario", "description": "Sviluppatore"}]
        store.index_records(records, cfg, "csv")

        assert mock_client.upsert.called
        upsert_kwargs = mock_client.upsert.call_args[1]
        points = upsert_kwargs["points"]
        assert len(points) == 1
        assert "_fts_text" in points[0].payload
        fts_text = points[0].payload["_fts_text"]
        assert "Mario" in fts_text
        assert "Sviluppatore" in fts_text

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_index_records_uuid_is_str(self, MockClient):
        """PointStruct.id is a string (str(uuid5(...)))."""
        import uuid as _uuid
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = True
        store = _make_store()
        store.open()

        records = [{"id": "42", "name": "Test"}]
        store.index_records(records, _make_cfg("fts"), "csv")

        upsert_kwargs = mock_client.upsert.call_args[1]
        point_id = upsert_kwargs["points"][0].id
        # Must be a valid UUID string (Pitfall 5)
        assert isinstance(point_id, str)
        parsed = _uuid.UUID(point_id)  # raises ValueError if invalid
        expected = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, "csv:42"))
        assert point_id == expected

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_index_records_skips_null_id(self, MockClient):
        """Records with None or empty id are skipped."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = True
        store = _make_store()
        store.open()

        records = [
            {"id": None, "name": "Skip me"},
            {"id": "", "name": "Skip me too"},
            {"id": "3", "name": "Include me"},
        ]
        store.index_records(records, _make_cfg("fts"), "csv")

        upsert_kwargs = mock_client.upsert.call_args[1]
        assert len(upsert_kwargs["points"]) == 1

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_index_records_returns_index_result(self, MockClient):
        """index_records returns IndexResult with correct counts."""
        from vector_stores.base import IndexResult
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = True
        store = _make_store()
        store.open()

        records = [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]
        result = store.index_records(records, _make_cfg("fts"), "csv")

        assert isinstance(result, IndexResult)
        assert result.inserted == 2


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

class TestLifecycle:
    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_open_creates_client(self, MockClient):
        """open() instantiates QdrantClient with the URL."""
        store = _make_store("http://localhost:6333")
        store.open()
        MockClient.assert_called_once_with(url="http://localhost:6333")

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_close_closes_client(self, MockClient):
        """close() calls client.close()."""
        mock_client = MockClient.return_value
        store = _make_store()
        store.open()
        store.close()
        mock_client.close.assert_called_once()

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_is_live_true(self, MockClient):
        """is_live returns True when get_collections succeeds."""
        mock_client = MockClient.return_value
        mock_client.get_collections.return_value = MagicMock()
        store = _make_store()
        store.open()
        assert store.is_live() is True

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_is_live_false_on_error(self, MockClient):
        """is_live returns False when get_collections raises."""
        mock_client = MockClient.return_value
        mock_client.get_collections.side_effect = Exception("unreachable")
        store = _make_store()
        store.open()
        assert store.is_live() is False

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_count(self, MockClient):
        """count() returns integer from client.count()."""
        mock_client = MockClient.return_value
        mock_count = MagicMock()
        mock_count.count = 42
        mock_client.count.return_value = mock_count
        store = _make_store()
        store.open()
        assert store.count("MyCollection") == 42

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_drop_index_calls_delete(self, MockClient):
        """drop_index calls client.delete_collection when collection exists."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = True
        store = _make_store()
        store.open()
        store.drop_index("MyCollection")
        mock_client.delete_collection.assert_called_once_with("MyCollection")

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_drop_index_no_op_when_missing(self, MockClient):
        """drop_index is a no-op when collection does not exist."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = False
        store = _make_store()
        store.open()
        store.drop_index("NonExistent")
        mock_client.delete_collection.assert_not_called()
