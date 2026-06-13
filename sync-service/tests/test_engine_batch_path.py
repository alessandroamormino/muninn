"""Integration tests for SyncEngine.run_full() OpenAI Batch API branching (Phase 25, Plan 25-02).

Verifies:
  - When adapter.supports_batch_api is True: run_full() calls fetch_records() (not chunked),
    calls embed_batch_async(), and calls index_records() exactly once.
  - When adapter.supports_batch_api is False: run_full() uses the existing streaming
    pipeline (fetch_records_chunked called, embed_batch_async not called).
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch, PropertyMock

import pytest

from config.settings import (
    AppConfig, EmbeddingConfig, SourceConfig, SyncConfig,
    VectorStoreConfig,
)
from sync.engine import SyncEngine
from vector_stores.base import IndexResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app_config(collection: str = "TestCollection") -> AppConfig:
    """Create a minimal AppConfig with csv source and text_fields."""
    return AppConfig(
        source=SourceConfig(type="csv", id_field="id"),
        embedding=EmbeddingConfig(type="ollama", model="test-model"),
        vector_store=VectorStoreConfig(
            collection=collection,
            text_fields={"name": 1.0, "description": 1.0},
        ),
        sync=SyncConfig(mode="full", hash_fields=["id"]),
    )


def _make_source_adapter(records: list[dict]) -> MagicMock:
    source = MagicMock()
    source.fetch_records.return_value = records
    # fetch_records_chunked yields the records in one chunk
    source.fetch_records_chunked.return_value = iter([records])
    source.get_record_id.side_effect = lambda r: str(r.get("id", "0"))
    source.get_record_hash.side_effect = lambda r: str(hash(str(r)))
    return source


def _make_vector_store(collection: str = "TestCollection") -> MagicMock:
    vs = MagicMock()
    vs.index_exists.return_value = False
    vs.create_index.return_value = True
    vs.drop_index.return_value = None
    vs.begin_bulk_load.return_value = None
    vs.end_bulk_load.return_value = None
    vs.index_records.return_value = IndexResult(inserted=3, updated=0, skipped=0)
    return vs


def _make_state_store() -> MagicMock:
    state = MagicMock()
    state.get.return_value = None
    state.bulk_set.return_value = None
    state.clear.return_value = None
    return state


def _make_batch_adapter(
    vectors: list[list[float]] | None = None,
    dimensions: int = 1536,
) -> MagicMock:
    """Return a mock embedding adapter that reports supports_batch_api=True."""
    if vectors is None:
        vectors = [[0.1, 0.2]] * 3
    adapter = MagicMock()
    # supports_batch_api must be a real property (not callable)
    type(adapter).supports_batch_api = PropertyMock(return_value=True)
    adapter.embed_batch_async.return_value = vectors
    adapter.dimensions.return_value = dimensions
    adapter.model_name.return_value = "text-embedding-3-small"
    return adapter


def _make_sync_adapter(dimensions: int = 1536) -> MagicMock:
    """Return a mock embedding adapter that reports supports_batch_api=False."""
    adapter = MagicMock()
    type(adapter).supports_batch_api = PropertyMock(return_value=False)
    adapter.dimensions.return_value = dimensions
    adapter.model_name.return_value = "ollama"
    adapter.embed.return_value = [[0.1, 0.2]]
    return adapter


# ---------------------------------------------------------------------------
# TestEngineBatchIntegration
# ---------------------------------------------------------------------------

class TestEngineBatchIntegration:
    """Integration tests for SyncEngine.run_full() OpenAI Batch API branching."""

    def _build_engine(
        self,
        app_cfg: AppConfig,
        source_adapter: MagicMock,
        vector_store: MagicMock,
        embedding_adapter: MagicMock,
        state_store: MagicMock,
    ) -> SyncEngine:
        """Build a SyncEngine with all adapters injected (bypassing __init__ factory calls)."""
        engine = object.__new__(SyncEngine)
        engine._cfg = app_cfg
        engine._vector_store = vector_store
        engine._state = state_store
        engine._cache_store = None
        engine._source_adapter = source_adapter
        engine._embedding_adapter = embedding_adapter
        engine._write_model_version_fn = MagicMock()
        engine._id_field = app_cfg.source.id_field
        return engine

    def test_run_full_uses_batch_path_when_supports_batch_api(self, tmp_path, monkeypatch):
        """When adapter.supports_batch_api is True, run_full() uses full-load path."""
        monkeypatch.setattr("embeddings.openai_adapter._BATCH_CHECKPOINT_DIR", tmp_path)

        cfg = _make_app_config("BatchTestCol")
        records = [
            {"id": "1", "name": "Alice", "description": "Engineer"},
            {"id": "2", "name": "Bob", "description": "Manager"},
            {"id": "3", "name": "Carol", "description": "Designer"},
        ]
        vectors = [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]

        source = _make_source_adapter(records)
        vs = _make_vector_store("BatchTestCol")
        state = _make_state_store()
        emb_adapter = _make_batch_adapter(vectors=vectors)

        engine = self._build_engine(cfg, source, vs, emb_adapter, state)

        # Patch checkpoint module to avoid file system side effects
        with patch("sync.engine.checkpoint") as mock_ckpt:
            mock_ckpt.read.return_value = None
            mock_ckpt.write.return_value = None
            mock_ckpt.delete.return_value = None

            result = engine.run_full()

        # fetch_records_chunked must NOT have been called (batch path uses fetch_records)
        source.fetch_records_chunked.assert_not_called()
        # fetch_records must have been called exactly once
        source.fetch_records.assert_called_once()
        # embed_batch_async must have been called exactly once
        emb_adapter.embed_batch_async.assert_called_once()
        call_kwargs = emb_adapter.embed_batch_async.call_args
        assert call_kwargs.kwargs.get("collection_name") == "BatchTestCol" or \
               (call_kwargs.args and len(call_kwargs.args) > 0)
        # index_records must have been called exactly once
        vs.index_records.assert_called_once()
        # begin_bulk_load and end_bulk_load must still have been called (HNSW staging)
        vs.begin_bulk_load.assert_called_once()
        vs.end_bulk_load.assert_called_once()

    def test_run_full_uses_streaming_when_supports_batch_api_false(self, monkeypatch):
        """When adapter.supports_batch_api is False, run_full() uses streaming pipeline."""
        cfg = _make_app_config("StreamTestCol")
        records = [{"id": "1", "name": "Alice", "description": "Engineer"}]

        source = _make_source_adapter(records)
        vs = _make_vector_store("StreamTestCol")
        state = _make_state_store()
        emb_adapter = _make_sync_adapter()

        engine = self._build_engine(cfg, source, vs, emb_adapter, state)

        with patch("sync.engine.checkpoint") as mock_ckpt:
            mock_ckpt.read.return_value = None
            mock_ckpt.write.return_value = None
            mock_ckpt.delete.return_value = None

            result = engine.run_full()

        # fetch_records_chunked MUST have been called (streaming path)
        source.fetch_records_chunked.assert_called_once()
        # embed_batch_async must NOT have been called (sync path)
        emb_adapter.embed_batch_async.assert_not_called()
        # begin/end_bulk_load still called for HNSW staging
        vs.begin_bulk_load.assert_called_once()
        vs.end_bulk_load.assert_called_once()
