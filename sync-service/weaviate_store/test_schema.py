"""Tests for _build_quantizer_config — SQ branch + HNSW kwargs (Phase 13.2, D-14/D-15/D-18/D-19)."""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from config.settings import HnswConfig, WeaviateConfig


def _make_cfg(quantization="none", ef=None, max_connections=None) -> WeaviateConfig:
    hnsw = HnswConfig(ef=ef, max_connections=max_connections)
    return WeaviateConfig(
        collection="TestCol",
        text_fields=["name"],
        metadata_fields=["id"],
        quantization=quantization,
        hnsw=hnsw,
    )


class TestBuildQuantizerConfigSQ:
    """_build_quantizer_config creates Scalar Quantization config for q=='sq' (D-14/D-15)."""

    def test_sq_branch_calls_quantizer_sq(self):
        """quantization='sq' → Configure.VectorIndex.hnsw called with Quantizer.sq()."""
        from weaviate_store.schema import _build_quantizer_config

        with patch("weaviate_store.schema._wvc") as mock_wvc:
            sq_sentinel = MagicMock(name="sq_result")
            mock_wvc.Configure.VectorIndex.Quantizer.sq.return_value = sq_sentinel
            _build_quantizer_config(_make_cfg(quantization="sq"))

        mock_wvc.Configure.VectorIndex.Quantizer.sq.assert_called_once_with(
            training_limit=100_000
        )
        mock_wvc.Configure.VectorIndex.hnsw.assert_called_once()
        _, hnsw_kwargs = mock_wvc.Configure.VectorIndex.hnsw.call_args
        assert hnsw_kwargs.get("quantizer") is sq_sentinel

    def test_sq_returns_hnsw_config(self):
        """_build_quantizer_config returns non-None for q=='sq'."""
        from weaviate_store.schema import _build_quantizer_config

        with patch("weaviate_store.schema._wvc") as mock_wvc:
            result = _build_quantizer_config(_make_cfg(quantization="sq"))

        assert result is not None


class TestBuildQuantizerConfigHnswKwargs:
    """HNSW kwargs (ef, max_connections) are passed only when non-None (D-18/D-19)."""

    def test_ef_passed_when_set(self):
        """ef=128 → Configure.VectorIndex.hnsw(ef=128, ...) called."""
        from weaviate_store.schema import _build_quantizer_config

        with patch("weaviate_store.schema._wvc") as mock_wvc:
            _build_quantizer_config(_make_cfg(quantization="none", ef=128))

        mock_wvc.Configure.VectorIndex.hnsw.assert_called_once()
        _, kwargs = mock_wvc.Configure.VectorIndex.hnsw.call_args
        assert kwargs.get("ef") == 128

    def test_max_connections_passed_when_set(self):
        """max_connections=32 → Configure.VectorIndex.hnsw(max_connections=32) called."""
        from weaviate_store.schema import _build_quantizer_config

        with patch("weaviate_store.schema._wvc") as mock_wvc:
            _build_quantizer_config(_make_cfg(quantization="none", max_connections=32))

        mock_wvc.Configure.VectorIndex.hnsw.assert_called_once()
        _, kwargs = mock_wvc.Configure.VectorIndex.hnsw.call_args
        assert kwargs.get("max_connections") == 32

    def test_none_fields_not_passed_as_kwargs(self):
        """ef=None + max_connections=None → Configure.VectorIndex.hnsw NOT called (returns None)."""
        from weaviate_store.schema import _build_quantizer_config

        with patch("weaviate_store.schema._wvc") as mock_wvc:
            result = _build_quantizer_config(_make_cfg(quantization="none"))

        # No HNSW override + no quantization → None returned
        assert result is None
        mock_wvc.Configure.VectorIndex.hnsw.assert_not_called()

    def test_sq_with_ef_passes_both(self):
        """sq + ef=200 → both quantizer and ef passed to hnsw()."""
        from weaviate_store.schema import _build_quantizer_config

        with patch("weaviate_store.schema._wvc") as mock_wvc:
            sq_sentinel = MagicMock(name="sq_result")
            mock_wvc.Configure.VectorIndex.Quantizer.sq.return_value = sq_sentinel
            _build_quantizer_config(_make_cfg(quantization="sq", ef=200))

        mock_wvc.Configure.VectorIndex.hnsw.assert_called_once()
        _, kwargs = mock_wvc.Configure.VectorIndex.hnsw.call_args
        assert kwargs.get("ef") == 200
        assert kwargs.get("quantizer") is sq_sentinel
