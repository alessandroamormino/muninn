"""Unit tests for vector_stores/weaviate_store.py — VS-02.

All tests use unittest.mock.patch to mock weaviate_store.client.get_client,
weaviate_store.upsert.upsert_records, etc. No live containers required.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock

import pytest


def _make_mock_cfg(collection: str = "TestCollection") -> MagicMock:
    """Build a minimal mock AppConfig."""
    cfg = MagicMock()
    cfg.weaviate.collection = collection
    cfg.weaviate.text_fields = ["title", "description"]
    cfg.weaviate.metadata_fields = ["id", "category"]
    cfg.embedding.type = "ollama"
    cfg.embedding.dims = 2560
    return cfg


class TestWeaviateVectorStoreInstantiation:
    def test_weaviate_satisfies_abc(self):
        """WeaviateVectorStore('http://localhost:8080') can be instantiated without TypeError."""
        from vector_stores.weaviate_store import WeaviateVectorStore
        vs = WeaviateVectorStore("http://localhost:8080")
        assert vs is not None


class TestWeaviateOpen:
    def test_open_calls_open_client(self):
        """open() delegates to weaviate_store.client.open_client()."""
        from vector_stores.weaviate_store import WeaviateVectorStore
        with patch("vector_stores.weaviate_store.open_client") as mock_open:
            vs = WeaviateVectorStore("http://localhost:8080")
            vs.open()
            mock_open.assert_called_once()


class TestWeaviateClose:
    def test_close_calls_close_client(self):
        """close() delegates to weaviate_store.client.close_client()."""
        from vector_stores.weaviate_store import WeaviateVectorStore
        with patch("vector_stores.weaviate_store.close_client") as mock_close:
            vs = WeaviateVectorStore("http://localhost:8080")
            vs.close()
            mock_close.assert_called_once()


class TestWeaviateCreateIndex:
    def test_create_index_delegates(self):
        """create_index(cfg) calls create_collection_if_missing with get_client() and weaviate_cfg."""
        from vector_stores.weaviate_store import WeaviateVectorStore
        mock_client = MagicMock()
        mock_cfg = _make_mock_cfg()
        with patch("vector_stores.weaviate_store.get_client", return_value=mock_client), \
             patch("vector_stores.weaviate_store.create_collection_if_missing", return_value=True) as mock_create:
            vs = WeaviateVectorStore("http://localhost:8080")
            result = vs.create_index(mock_cfg)
            mock_create.assert_called_once()
            # First positional arg is the client
            assert mock_create.call_args[0][0] is mock_client


class TestWeaviateDropIndex:
    def test_drop_index_delegates(self):
        """drop_index('MyCol') calls get_client().collections.delete('MyCol') when collection exists."""
        from vector_stores.weaviate_store import WeaviateVectorStore
        mock_client = MagicMock()
        mock_client.collections.exists.return_value = True
        with patch("vector_stores.weaviate_store.get_client", return_value=mock_client):
            vs = WeaviateVectorStore("http://localhost:8080")
            vs.drop_index("MyCol")
            mock_client.collections.delete.assert_called_once_with("MyCol")

    def test_drop_index_no_op_when_missing(self):
        """drop_index does not call delete when collection does not exist."""
        from vector_stores.weaviate_store import WeaviateVectorStore
        mock_client = MagicMock()
        mock_client.collections.exists.return_value = False
        with patch("vector_stores.weaviate_store.get_client", return_value=mock_client):
            vs = WeaviateVectorStore("http://localhost:8080")
            vs.drop_index("NoSuchCol")
            mock_client.collections.delete.assert_not_called()


class TestWeaviateIsLive:
    def test_is_live_delegates(self):
        """is_live() returns get_client().is_live() result."""
        from vector_stores.weaviate_store import WeaviateVectorStore
        mock_client = MagicMock()
        mock_client.is_live.return_value = True
        with patch("vector_stores.weaviate_store.get_client", return_value=mock_client):
            vs = WeaviateVectorStore("http://localhost:8080")
            assert vs.is_live() is True

    def test_is_live_returns_false_on_exception(self):
        """is_live() returns False if get_client() raises."""
        from vector_stores.weaviate_store import WeaviateVectorStore
        with patch("vector_stores.weaviate_store.get_client", side_effect=RuntimeError("not open")):
            vs = WeaviateVectorStore("http://localhost:8080")
            assert vs.is_live() is False


class TestWeaviateCount:
    def test_count_delegates(self):
        """count('MyCol') returns aggregate.total_count via get_client().collections.get().aggregate.over_all()."""
        from vector_stores.weaviate_store import WeaviateVectorStore
        mock_client = MagicMock()
        mock_agg = MagicMock()
        mock_agg.total_count = 42
        mock_client.collections.get.return_value.aggregate.over_all.return_value = mock_agg
        with patch("vector_stores.weaviate_store.get_client", return_value=mock_client):
            vs = WeaviateVectorStore("http://localhost:8080")
            result = vs.count("MyCol")
            assert result == 42

    def test_count_returns_none_on_exception(self):
        """count() returns None when Weaviate raises."""
        from vector_stores.weaviate_store import WeaviateVectorStore
        mock_client = MagicMock()
        mock_client.collections.get.side_effect = Exception("not found")
        with patch("vector_stores.weaviate_store.get_client", return_value=mock_client):
            vs = WeaviateVectorStore("http://localhost:8080")
            assert vs.count("NoSuchCol") is None


class TestWeaviateSearch:
    def test_search_returns_search_hits(self):
        """search(q, vec, cfg) returns list[SearchHit] derived from weaviate results.objects."""
        from vector_stores.weaviate_store import WeaviateVectorStore
        from vector_stores.base import SearchHit

        mock_obj = MagicMock()
        mock_obj.properties = {"title": "Test", "id": "1"}
        mock_obj.metadata.score = 0.85

        mock_results = MagicMock()
        mock_results.objects = [mock_obj]

        mock_col = MagicMock()
        mock_col.query.hybrid.return_value = mock_results

        mock_client = MagicMock()
        mock_client.collections.get.return_value = mock_col

        mock_cfg = _make_mock_cfg()

        with patch("vector_stores.weaviate_store.get_client", return_value=mock_client):
            vs = WeaviateVectorStore("http://localhost:8080")
            hits = vs.search("test query", [0.1, 0.2], mock_cfg, limit=5)
            assert len(hits) == 1
            assert isinstance(hits[0], SearchHit)
            assert hits[0].score == 0.85
            assert hits[0].properties["title"] == "Test"


class TestWeaviateGetVectorsForGraph:
    def test_get_vectors_for_graph_returns_list(self):
        """get_vectors_for_graph('Col', 100) returns list of dicts with 'vector' and 'payload' keys."""
        from vector_stores.weaviate_store import WeaviateVectorStore

        mock_obj = MagicMock()
        mock_obj.vector = {"default": [0.1, 0.2, 0.3]}
        mock_obj.uuid = "abc-123"
        mock_obj.properties = {"title": "Item", "id": "1"}

        mock_col = MagicMock()
        mock_col.iterator.return_value = iter([mock_obj])

        mock_client = MagicMock()
        mock_client.collections.get.return_value = mock_col

        with patch("vector_stores.weaviate_store.get_client", return_value=mock_client):
            vs = WeaviateVectorStore("http://localhost:8080")
            result = vs.get_vectors_for_graph("Col", max_nodes=100)

        assert result is not None
        assert len(result) == 1
        assert "vector" in result[0]
        assert "payload" in result[0]
        assert result[0]["vector"] == [0.1, 0.2, 0.3]


class TestWeaviateIndexRecords:
    def test_index_records_delegates_to_upsert_records(self):
        """index_records(...) delegates to weaviate_store.upsert.upsert_records() and returns IndexResult."""
        from vector_stores.weaviate_store import WeaviateVectorStore
        from vector_stores.base import IndexResult

        from weaviate_store.upsert import UpsertResult
        mock_upsert_result = UpsertResult(inserted=3, updated=1, skipped=0)

        mock_client = MagicMock()
        mock_cfg = _make_mock_cfg()
        mock_embedding = MagicMock()
        records = [{"id": "1", "title": "Test"}]

        with patch("vector_stores.weaviate_store.get_client", return_value=mock_client), \
             patch("vector_stores.weaviate_store.upsert_records", return_value=mock_upsert_result) as mock_upsert:
            vs = WeaviateVectorStore("http://localhost:8080")
            result = vs.index_records(
                records, mock_cfg, "csv", mock_embedding, id_field="id"
            )
            mock_upsert.assert_called_once()
            assert isinstance(result, IndexResult)
            assert result.inserted == 3
            assert result.updated == 1
            assert result.skipped == 0
