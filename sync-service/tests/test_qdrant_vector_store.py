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
              text_fields: dict | list | None = None, fts_language: str = "en",
              match_mode: str = "and"):
    cfg = MagicMock()
    cfg.vector_store.collection = collection
    cfg.vector_store.search_mode = search_mode
    # Accept list for backward compat in tests, normalize to dict
    tf = text_fields if text_fields is not None else {"name": 1.0, "description": 1.0}
    if isinstance(tf, list):
        tf = {f: 1.0 for f in tf}
    cfg.vector_store.text_fields = tf
    cfg.vector_store.metadata_fields = ["ruolo"]
    cfg.vector_store.fts.language = fts_language
    cfg.vector_store.fts.match_mode = match_mode
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

        store.create_index(_make_cfg("bm25", text_fields={"description": 1.0}))

        call_kwargs = mock_client.create_collection.call_args[1]
        # No dense vectors for bm25
        assert "dense" not in call_kwargs.get("vectors_config", {})
        # Sparse present (single-field → legacy "sparse" key)
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

        store.create_index(_make_cfg("fts", text_fields={"description": 1.0}))

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

        store.search("query text", None, _make_cfg("bm25", text_fields={"description": 1.0}), limit=5, mode="bm25")

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

        store.search("query text", None, _make_cfg("fts", text_fields={"description": 1.0}), limit=5, mode="fts")

        call_kwargs = mock_client.query_points.call_args[1]
        # CRITICAL: must use sparse BM25, NOT MatchText filter as the main query (PITFALL 1)
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
        # match_mode pre-filter adds a _fts_text condition; equality filter also present
        eq_conds = [c for c in must if hasattr(c, "key") and c.key == "ruolo"]
        assert len(eq_conds) == 1
        assert eq_conds[0].match.value == "Sviluppatore"


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
    def test_index_records_fts_calls_on_batch_done(self, MockClient):
        """fts mode: on_batch_done called for each parallel batch; total equals record count."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = True
        store = _make_store()
        store.open()

        records = [{"id": str(i), "name": f"rec{i}"} for i in range(3)]
        progress_calls = []
        store.index_records(
            records, _make_cfg("fts"), "csv",
            on_batch_done=lambda bn, done, total: progress_calls.append((done, total)),
        )

        assert len(progress_calls) >= 1
        assert progress_calls[-1][1] == 3  # total == 3

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_index_records_bm25_calls_on_batch_done(self, MockClient):
        """bm25 mode: on_batch_done called for each parallel batch; total equals record count."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = True
        store = _make_store()
        store.open()

        records = [{"id": str(i), "name": f"rec{i}"} for i in range(5)]
        progress_calls = []
        store.index_records(
            records, _make_cfg("bm25"), "csv",
            on_batch_done=lambda bn, done, total: progress_calls.append((done, total)),
        )

        assert len(progress_calls) >= 1
        assert progress_calls[-1][1] == 5

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_index_records_bm25_skips_embedding(self, MockClient):
        """bm25 mode: embedding_adapter.embed() is NEVER called (sparse BM25 only)."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = True
        embedding_adapter = MagicMock()
        store = _make_store()
        store.open()

        records = [{"id": "1", "name": "Mario", "description": "Test"}]
        store.index_records(records, _make_cfg("bm25"), "csv", embedding_adapter=embedding_adapter)

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


# ---------------------------------------------------------------------------
# Phase 23: TestFieldWeights — multi-sparse schema + per-field upsert + RRF
# ---------------------------------------------------------------------------

class TestFieldWeights:
    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_multi_sparse_schema_created_for_two_fields(self, MockClient):
        """2 text_fields → sparse_description + sparse_tags in schema (not 'sparse')."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = False
        store = _make_store()
        store.open()
        cfg = _make_cfg("fts", text_fields={"description": 1.0, "tags": 0.5})

        store.create_index(cfg)

        call_kwargs = mock_client.create_collection.call_args[1]
        sparse = call_kwargs["sparse_vectors_config"]
        assert "sparse_description" in sparse
        assert "sparse_tags" in sparse
        assert "sparse" not in sparse  # legacy single-name must NOT appear

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_single_field_keeps_legacy_sparse_name(self, MockClient):
        """1 text_field → schema uses legacy 'sparse' name (backward compat)."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = False
        store = _make_store()
        store.open()
        cfg = _make_cfg("fts", text_fields={"description": 1.0})

        store.create_index(cfg)

        call_kwargs = mock_client.create_collection.call_args[1]
        sparse = call_kwargs["sparse_vectors_config"]
        assert "sparse" in sparse
        assert "sparse_description" not in sparse

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_empty_text_fields_keeps_legacy_sparse_name(self, MockClient):
        """Empty text_fields dict → schema uses legacy 'sparse' (safe no-op)."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = False
        store = _make_store()
        store.open()
        cfg = _make_cfg("fts", text_fields={})

        store.create_index(cfg)

        call_kwargs = mock_client.create_collection.call_args[1]
        sparse = call_kwargs["sparse_vectors_config"]
        assert "sparse" in sparse

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_multi_sparse_index_records_writes_per_field_documents(self, MockClient):
        """index_records with 2 text_fields → upsert points have sparse_description + sparse_tags vectors."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = True
        # Mock scroll for vocab build (called after index_records in fts mode)
        mock_client.scroll.return_value = ([], None)
        store = _make_store()
        store.open()
        cfg = _make_cfg("fts", text_fields={"description": 1.0, "tags": 0.5})
        records = [{"id": "1", "description": "tavolo in legno", "tags": "mobili"}]

        store.index_records(records, cfg, "csv")

        assert mock_client.upsert.called
        upsert_kwargs = mock_client.upsert.call_args[1]
        points = upsert_kwargs["points"]
        assert len(points) == 1
        vec = points[0].vector
        assert "sparse_description" in vec
        assert "sparse_tags" in vec
        assert "sparse" not in vec
        assert isinstance(vec["sparse_description"], qmodels.Document)
        assert isinstance(vec["sparse_tags"], qmodels.Document)

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_single_sparse_index_records_uses_joined_fts_text(self, MockClient):
        """Single text_field → index_records uses 'sparse' key with joined _fts_text (backward compat)."""
        mock_client = MockClient.return_value
        mock_client.collection_exists.return_value = True
        mock_client.scroll.return_value = ([], None)
        store = _make_store()
        store.open()
        cfg = _make_cfg("fts", text_fields={"description": 1.0})
        records = [{"id": "1", "description": "tavolo in legno"}]

        store.index_records(records, cfg, "csv")

        upsert_kwargs = mock_client.upsert.call_args[1]
        points = upsert_kwargs["points"]
        vec = points[0].vector
        assert "sparse" in vec
        assert "sparse_description" not in vec

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_search_multi_field_uses_rrf_query_with_weights(self, MockClient):
        """Multi-field BM25 search uses RrfQuery(rrf=Rrf(weights=[...])) — NOT FusionQuery (Pitfall 2)."""
        mock_client = MockClient.return_value
        mock_results = MagicMock()
        mock_results.points = []
        mock_client.query_points.return_value = mock_results
        store = _make_store()
        store.open()
        cfg = _make_cfg("bm25", text_fields={"description": 1.0, "tags": 0.5})

        store.search("query text", None, cfg, limit=5, mode="bm25")

        call_kwargs = mock_client.query_points.call_args[1]
        assert "prefetch" in call_kwargs
        assert len(call_kwargs["prefetch"]) == 2
        assert isinstance(call_kwargs["query"], qmodels.RrfQuery)
        assert call_kwargs["query"].rrf.weights == [1.0, 0.5]

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_search_single_field_uses_legacy_fusion_query(self, MockClient):
        """Single-field BM25 search uses Document + using='sparse' (no RRF, backward compat)."""
        mock_client = MockClient.return_value
        mock_results = MagicMock()
        mock_results.points = []
        mock_client.query_points.return_value = mock_results
        store = _make_store()
        store.open()
        cfg = _make_cfg("bm25", text_fields={"description": 1.0})

        store.search("query text", None, cfg, limit=5, mode="bm25")

        call_kwargs = mock_client.query_points.call_args[1]
        assert call_kwargs.get("using") == "sparse"
        assert isinstance(call_kwargs.get("query"), qmodels.Document)
        assert "prefetch" not in call_kwargs


# ---------------------------------------------------------------------------
# Phase 23: TestMatchMode — AND/OR match mode filter on _fts_text
# ---------------------------------------------------------------------------

class TestMatchMode:
    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_search_fts_and_adds_match_text_filter(self, MockClient):
        """fts mode + match_mode='and' → FieldCondition with MatchText in query_filter.must."""
        mock_client = MockClient.return_value
        mock_results = MagicMock()
        mock_results.points = []
        mock_client.query_points.return_value = mock_results
        store = _make_store()
        store.open()
        cfg = _make_cfg("fts", match_mode="and")

        store.search("hello world", None, cfg, limit=5, mode="fts")

        call_kwargs = mock_client.query_points.call_args[1]
        qdrant_filter = call_kwargs.get("query_filter")
        assert qdrant_filter is not None
        must = qdrant_filter.must
        fts_conds = [c for c in must if hasattr(c, "key") and c.key == "_fts_text"]
        assert len(fts_conds) == 1
        assert isinstance(fts_conds[0].match, qmodels.MatchText)

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_search_fts_or_adds_match_text_any_filter(self, MockClient):
        """fts mode + match_mode='or' → FieldCondition with MatchTextAny in query_filter.must."""
        mock_client = MockClient.return_value
        mock_results = MagicMock()
        mock_results.points = []
        mock_client.query_points.return_value = mock_results
        store = _make_store()
        store.open()
        cfg = _make_cfg("fts", match_mode="or")

        store.search("hello world", None, cfg, limit=5, mode="fts")

        call_kwargs = mock_client.query_points.call_args[1]
        qdrant_filter = call_kwargs.get("query_filter")
        assert qdrant_filter is not None
        must = qdrant_filter.must
        fts_conds = [c for c in must if hasattr(c, "key") and c.key == "_fts_text"]
        assert len(fts_conds) == 1
        assert isinstance(fts_conds[0].match, qmodels.MatchTextAny)

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_search_match_mode_override_param_wins(self, MockClient):
        """match_mode_override='or' wins over cfg match_mode='and'."""
        mock_client = MockClient.return_value
        mock_results = MagicMock()
        mock_results.points = []
        mock_client.query_points.return_value = mock_results
        store = _make_store()
        store.open()
        cfg = _make_cfg("fts", match_mode="and")

        store.search("hello world", None, cfg, limit=5, mode="fts", match_mode_override="or")

        call_kwargs = mock_client.query_points.call_args[1]
        qdrant_filter = call_kwargs.get("query_filter")
        must = qdrant_filter.must
        fts_conds = [c for c in must if hasattr(c, "key") and c.key == "_fts_text"]
        assert isinstance(fts_conds[0].match, qmodels.MatchTextAny)

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_search_bm25_mode_applies_match_mode_filter(self, MockClient):
        """bm25 mode also applies AND/OR match_mode filter on _fts_text."""
        mock_client = MockClient.return_value
        mock_results = MagicMock()
        mock_results.points = []
        mock_client.query_points.return_value = mock_results
        store = _make_store()
        store.open()
        cfg = _make_cfg("bm25", match_mode="or", text_fields={"description": 1.0})

        store.search("hello", None, cfg, limit=5, mode="bm25")

        call_kwargs = mock_client.query_points.call_args[1]
        qdrant_filter = call_kwargs.get("query_filter")
        assert qdrant_filter is not None
        must = qdrant_filter.must
        fts_conds = [c for c in must if hasattr(c, "key") and c.key == "_fts_text"]
        assert len(fts_conds) == 1
        assert isinstance(fts_conds[0].match, qmodels.MatchTextAny)

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_search_hybrid_mode_does_not_apply_match_mode_filter(self, MockClient):
        """hybrid mode → NO MatchText/MatchTextAny filter added to query_filter.must."""
        mock_client = MockClient.return_value
        mock_results = MagicMock()
        mock_results.points = []
        mock_client.query_points.return_value = mock_results
        store = _make_store()
        store.open()
        cfg = _make_cfg("hybrid", match_mode="and")

        store.search("hello world", [0.1, 0.2], cfg, limit=5, mode="hybrid")

        call_kwargs = mock_client.query_points.call_args[1]
        qdrant_filter = call_kwargs.get("query_filter")
        # No filter should be set (or if set, should contain no MatchText/_fts_text cond)
        if qdrant_filter is not None and qdrant_filter.must:
            fts_conds = [
                c for c in qdrant_filter.must
                if hasattr(c, "key") and c.key == "_fts_text"
            ]
            assert len(fts_conds) == 0
        # else: no filter at all is also correct

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_search_match_mode_filter_skipped_when_must_not_present(self, MockClient):
        """must_not_text_terms path (scroll) must NOT add the match_text pre-filter."""
        mock_client = MockClient.return_value
        mock_scroll_points = [MagicMock()]
        mock_scroll_points[0].payload = {"name": "Mario"}
        mock_client.scroll.return_value = (mock_scroll_points, None)
        store = _make_store()
        store.open()
        cfg = _make_cfg("fts", match_mode="and", text_fields={"name": 1.0})

        store.search("hello", None, cfg, limit=5, mode="fts",
                     must_not_text_terms=["foo"])

        # scroll was used (not query_points)
        assert mock_client.scroll.called
        assert not mock_client.query_points.called


# ---------------------------------------------------------------------------
# Phase 23: TestFuzzyVocab — _fuzzy_vocab populated after index_records
# ---------------------------------------------------------------------------

from types import SimpleNamespace


def _mock_point(fts_text: str):
    p = SimpleNamespace()
    p.payload = {"_fts_text": fts_text}
    return p


class TestFuzzyVocab:
    def setup_method(self):
        from vector_stores import qdrant_store
        qdrant_store._fuzzy_vocab.clear()

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_index_records_fts_mode_populates_fuzzy_vocab(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.scroll.return_value = ([_mock_point("hello world")], None)
        store = _make_store(); store.open()
        cfg = _make_cfg("fts", text_fields={"description": 1.0})
        store.index_records([{"id": "1", "description": "hello world"}], cfg, "csv")
        from vector_stores.qdrant_store import _fuzzy_vocab
        assert isinstance(_fuzzy_vocab.get("TestCollection"), frozenset)
        assert len(_fuzzy_vocab["TestCollection"]) > 0

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_index_records_bm25_mode_populates_fuzzy_vocab(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.scroll.return_value = ([_mock_point("foo bar")], None)
        store = _make_store(); store.open()
        cfg = _make_cfg("bm25", text_fields={"description": 1.0})
        store.index_records([{"id": "1", "description": "foo bar"}], cfg, "csv")
        from vector_stores.qdrant_store import _fuzzy_vocab
        assert isinstance(_fuzzy_vocab.get("TestCollection"), frozenset)

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_index_records_hybrid_mode_skips_fuzzy_vocab(self, MockClient):
        mock_client = MockClient.return_value
        store = _make_store(); store.open()
        cfg = _make_cfg("hybrid", text_fields={"description": 1.0})
        store.index_records([{"id": "1", "description": "test"}], cfg, "csv", embedding_adapter=None)
        from vector_stores.qdrant_store import _fuzzy_vocab
        assert _fuzzy_vocab.get("TestCollection") is None

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_fuzzy_vocab_tokenizes_on_whitespace_and_punctuation(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.scroll.return_value = ([_mock_point("Tavolo, in legno!")], None)
        store = _make_store(); store.open()
        cfg = _make_cfg("fts", text_fields={"description": 1.0})
        store.index_records([{"id": "1", "description": "Tavolo, in legno!"}], cfg, "csv")
        from vector_stores.qdrant_store import _fuzzy_vocab
        vocab = _fuzzy_vocab["TestCollection"]
        assert "tavolo" in vocab
        assert "in" in vocab
        assert "legno" in vocab

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_fuzzy_vocab_capped_at_50k_tokens(self, MockClient):
        from vector_stores.qdrant_store import _FUZZY_VOCAB_CAP
        unique_words = " ".join(f"tok{i}" for i in range(_FUZZY_VOCAB_CAP + 1000))
        mock_client = MockClient.return_value
        mock_client.scroll.return_value = ([_mock_point(unique_words)], None)
        store = _make_store(); store.open()
        cfg = _make_cfg("fts", text_fields={"description": 1.0})
        store.index_records([{"id": "1", "description": "test"}], cfg, "csv")
        from vector_stores.qdrant_store import _fuzzy_vocab
        assert len(_fuzzy_vocab["TestCollection"]) <= _FUZZY_VOCAB_CAP

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_fuzzy_vocab_scroll_error_leaves_vocab_unchanged(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.scroll.side_effect = Exception("scroll error")
        store = _make_store(); store.open()
        cfg = _make_cfg("fts", text_fields={"description": 1.0})
        from vector_stores import qdrant_store
        qdrant_store._fuzzy_vocab["TestCollection"] = frozenset(["existing"])
        store.index_records([{"id": "1", "description": "test"}], cfg, "csv")
        assert qdrant_store._fuzzy_vocab.get("TestCollection") == frozenset(["existing"])


# ---------------------------------------------------------------------------
# Phase 23: TestSynonymsPayload — _synonyms written at index time
# ---------------------------------------------------------------------------

class TestSynonymsPayload:
    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_index_records_use_omw_true_writes_synonyms_payload(self, MockClient, monkeypatch):
        monkeypatch.setattr("vector_stores.qdrant_store._get_omw_synonyms",
                            lambda token, lang: ["sinonimo1"] if token == "tavolo" else [])
        mock_client = MockClient.return_value
        mock_client.scroll.return_value = ([], None)
        store = _make_store(); store.open()
        cfg = _make_cfg("fts", text_fields={"description": 1.0})
        cfg.vector_store.fts.use_omw = True
        cfg.vector_store.fts.language = "it"
        store.index_records([{"id": "1", "description": "tavolo"}], cfg, "csv")
        points = mock_client.upsert.call_args[1]["points"]
        assert "_synonyms" in points[0].payload
        assert "sinonimo1" in points[0].payload["_synonyms"]

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_index_records_use_omw_false_writes_empty_synonyms_list(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.scroll.return_value = ([], None)
        store = _make_store(); store.open()
        cfg = _make_cfg("fts", text_fields={"description": 1.0})
        cfg.vector_store.fts.use_omw = False
        store.index_records([{"id": "1", "description": "tavolo"}], cfg, "csv")
        points = mock_client.upsert.call_args[1]["points"]
        assert "_synonyms" in points[0].payload
        assert points[0].payload["_synonyms"] == []

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_synonyms_deduplicated_across_tokens(self, MockClient, monkeypatch):
        monkeypatch.setattr("vector_stores.qdrant_store._get_omw_synonyms",
                            lambda token, lang: ["comune"])
        mock_client = MockClient.return_value
        mock_client.scroll.return_value = ([], None)
        store = _make_store(); store.open()
        cfg = _make_cfg("fts", text_fields={"description": 1.0})
        cfg.vector_store.fts.use_omw = True
        cfg.vector_store.fts.language = "it"
        store.index_records([{"id": "1", "description": "alfa beta"}], cfg, "csv")
        points = mock_client.upsert.call_args[1]["points"]
        syns = points[0].payload["_synonyms"]
        assert syns.count("comune") == 1

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_synonyms_capped_at_50_total(self, MockClient, monkeypatch):
        monkeypatch.setattr("vector_stores.qdrant_store._get_omw_synonyms",
                            lambda token, lang: [f"syn{i}" for i in range(100)])
        mock_client = MockClient.return_value
        mock_client.scroll.return_value = ([], None)
        store = _make_store(); store.open()
        cfg = _make_cfg("fts", text_fields={"description": 1.0})
        cfg.vector_store.fts.use_omw = True
        cfg.vector_store.fts.language = "it"
        store.index_records([{"id": "1", "description": "tok1 tok2 tok3"}], cfg, "csv")
        points = mock_client.upsert.call_args[1]["points"]
        assert len(points[0].payload["_synonyms"]) <= 50

    @patch("vector_stores.qdrant_store.QdrantClient")
    def test_synonyms_payload_skipped_for_hybrid_mode(self, MockClient, monkeypatch):
        monkeypatch.setattr("vector_stores.qdrant_store._get_omw_synonyms",
                            lambda token, lang: ["syn"])
        mock_client = MockClient.return_value
        store = _make_store(); store.open()
        cfg = _make_cfg("hybrid", text_fields={"description": 1.0})
        cfg.vector_store.fts.use_omw = True
        store.index_records([{"id": "1", "description": "test"}], cfg, "csv", embedding_adapter=None)
        if mock_client.upsert.called:
            points = mock_client.upsert.call_args[1]["points"]
            for p in points:
                assert "_synonyms" not in (p.payload or {})
