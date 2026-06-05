"""Tests for upsert_records embedding deduplication (Phase 13.2, D-08..D-12)."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from config.settings import VectorStoreConfig


def _make_weaviate_cfg(text_fields=("name",), metadata_fields=("id",)) -> VectorStoreConfig:
    return VectorStoreConfig(
        collection="TestCol",
        text_fields=list(text_fields),
        metadata_fields=list(metadata_fields),
    )


def _make_mock_client() -> MagicMock:
    """Mock Weaviate client where data.exists always returns False (every record is an insert)."""
    mock_client = MagicMock()
    mock_client.collections.get.return_value.data.exists.return_value = False
    return mock_client


class TestEmbeddingDedup:
    """upsert_records deduplicates identical texts before calling the embedding adapter (D-08..D-12)."""

    def test_dedup_reduces_embed_calls_on_duplicate_docs(self):
        """Two records with identical text -> embed called once with unique_docs only."""
        from weaviate_store.upsert import upsert_records

        records = [
            {"id": "1", "name": "Alice"},
            {"id": "2", "name": "Alice"},  # duplicate text
        ]
        cfg = _make_weaviate_cfg()
        mock_adapter = MagicMock()
        mock_adapter.embed.return_value = [[0.1, 0.2]]  # one vector for one unique doc

        upsert_records(_make_mock_client(), records, cfg, "test", mock_adapter, id_field="id")

        # embed should be called exactly once with unique_docs only
        assert mock_adapter.embed.call_count == 1
        called_docs = mock_adapter.embed.call_args[0][0]
        assert called_docs == ["Alice"]

    def test_no_dedup_when_all_docs_unique(self):
        """All distinct texts -> embed called once with the full list."""
        from weaviate_store.upsert import upsert_records

        records = [
            {"id": "1", "name": "Alice"},
            {"id": "2", "name": "Bob"},
        ]
        cfg = _make_weaviate_cfg()
        mock_adapter = MagicMock()
        mock_adapter.embed.return_value = [[0.1], [0.2]]

        upsert_records(_make_mock_client(), records, cfg, "test", mock_adapter, id_field="id")

        assert mock_adapter.embed.call_count == 1
        called_docs = mock_adapter.embed.call_args[0][0]
        assert len(called_docs) == 2
        assert set(called_docs) == {"Alice", "Bob"}

    def test_dedup_log_emitted_only_when_duplicates_exist(self, caplog):
        """logger.info dedup message appears only when duplicates found (D-12)."""
        from weaviate_store.upsert import upsert_records

        records = [{"id": "1", "name": "Alice"}, {"id": "2", "name": "Alice"}]
        cfg = _make_weaviate_cfg()
        mock_adapter = MagicMock()
        mock_adapter.embed.return_value = [[0.1]]

        with caplog.at_level(logging.INFO, logger="weaviate_store.upsert"):
            upsert_records(_make_mock_client(), records, cfg, "test", mock_adapter, id_field="id")

        assert any("Embedding dedup" in r.message for r in caplog.records)

    def test_dedup_no_log_when_no_duplicates(self, caplog):
        """No dedup log message when all texts are unique (D-12)."""
        from weaviate_store.upsert import upsert_records

        records = [{"id": "1", "name": "Alice"}, {"id": "2", "name": "Bob"}]
        cfg = _make_weaviate_cfg()
        mock_adapter = MagicMock()
        mock_adapter.embed.return_value = [[0.1], [0.2]]

        with caplog.at_level(logging.INFO, logger="weaviate_store.upsert"):
            upsert_records(_make_mock_client(), records, cfg, "test", mock_adapter, id_field="id")

        assert not any("Embedding dedup" in r.message for r in caplog.records)
