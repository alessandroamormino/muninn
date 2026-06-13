"""Unit tests for OpenAIEmbeddingAdapter (Phase 25, SC-1, SC-2, SC-4, SC-5)."""
from __future__ import annotations

import logging
import os
from unittest.mock import MagicMock, patch

import pytest

import openai
from config.settings import EmbeddingConfig
from embeddings import build_embedding_adapter
from embeddings.openai_adapter import (
    OpenAIEmbeddingAdapter,
    OpenAIEmbeddingError,
    _mask_key,
    _resolve_env_var,
)


# ---------------------------------------------------------------------------
# Module-level helpers shared across test classes
# ---------------------------------------------------------------------------

def _make_embedding_response(vectors: list[list[float]]) -> MagicMock:
    """Build a mock openai embeddings response object."""
    resp = MagicMock()
    resp.data = [MagicMock(embedding=v, index=i) for i, v in enumerate(vectors)]
    return resp


def _make_cfg(**overrides) -> EmbeddingConfig:
    """Return an EmbeddingConfig with sensible openai defaults."""
    defaults = dict(type="openai", model="text-embedding-3-small", api_key="sk-test-key")
    defaults.update(overrides)
    return EmbeddingConfig(**defaults)


# ---------------------------------------------------------------------------
# SC-1: Factory wiring
# ---------------------------------------------------------------------------

class TestFactory:
    """Verify build_embedding_adapter dispatches correctly for type='openai'."""

    def test_build_embedding_adapter_returns_openai_instance(self):
        cfg = EmbeddingConfig(type="openai", api_key="sk-test")
        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = build_embedding_adapter(cfg)
        assert isinstance(result, OpenAIEmbeddingAdapter)

    def test_factory_returns_none_for_weaviate_builtin(self):
        """Regression: weaviate_builtin must still return None."""
        assert build_embedding_adapter(EmbeddingConfig(type="weaviate_builtin")) is None


# ---------------------------------------------------------------------------
# SC-2: Dimensions lookup
# ---------------------------------------------------------------------------

class TestDimensions:
    """Verify dimension lookup from _MODEL_DIMS without API calls."""

    def test_small_returns_1536_without_api_call(self):
        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            adapter = OpenAIEmbeddingAdapter(_make_cfg(model="text-embedding-3-small"))
            result = adapter.dimensions()
        assert result == 1536
        assert mock_client.embeddings.create.call_count == 0

    def test_large_returns_3072_without_api_call(self):
        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            adapter = OpenAIEmbeddingAdapter(_make_cfg(model="text-embedding-3-large"))
            result = adapter.dimensions()
        assert result == 3072
        assert mock_client.embeddings.create.call_count == 0

    def test_unknown_model_probes_and_caches(self):
        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.embeddings.create.return_value = _make_embedding_response(
                [[0.1] * 768]
            )
            adapter = OpenAIEmbeddingAdapter(_make_cfg(model="text-embedding-future"))
            # First call — should probe
            dim1 = adapter.dimensions()
            assert dim1 == 768
            # Second call — should use cache, no extra API call
            dim2 = adapter.dimensions()
            assert dim2 == 768
            assert mock_client.embeddings.create.call_count == 1  # cached


# ---------------------------------------------------------------------------
# SC-2: ${VAR} environment variable resolution
# ---------------------------------------------------------------------------

class TestVarResolution:
    """Verify _resolve_env_var and adapter key resolution."""

    def test_resolve_env_var_dollar_brace(self, monkeypatch):
        monkeypatch.setenv("TEST_K", "sk-from-env")
        assert _resolve_env_var("${TEST_K}") == "sk-from-env"

    def test_resolve_env_var_literal(self):
        assert _resolve_env_var("sk-literal") == "sk-literal"

    def test_resolve_env_var_none(self):
        assert _resolve_env_var(None) is None

    def test_adapter_resolves_env_var_at_init(self, monkeypatch):
        monkeypatch.setenv("TEST_K2", "sk-from-env-2")
        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            OpenAIEmbeddingAdapter(_make_cfg(api_key="${TEST_K2}"))
            # SDK client must have been built with the resolved key
            mock_cls.assert_called_once_with(api_key="sk-from-env-2", max_retries=0)

    def test_missing_key_raises_value_error(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key not found"):
            OpenAIEmbeddingAdapter(_make_cfg(api_key=None))


# ---------------------------------------------------------------------------
# SC-4: Rate-limit retry with WARNING logging
# ---------------------------------------------------------------------------

class TestRetry:
    """Verify exponential-backoff retry on openai.RateLimitError."""

    def test_429_retry_logs_warning_per_attempt(self, caplog):
        cfg = _make_cfg(max_retries=2)
        rl_err1 = openai.RateLimitError("rate limit")
        rl_err1.response = MagicMock(headers={})
        rl_err2 = openai.RateLimitError("rate limit")
        rl_err2.response = MagicMock(headers={})

        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls, \
             patch("embeddings.openai_adapter.time.sleep"), \
             caplog.at_level(logging.WARNING, logger="embeddings.openai_adapter"):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.embeddings.create.side_effect = [
                rl_err1,
                rl_err2,
                _make_embedding_response([[0.1]]),
            ]
            adapter = OpenAIEmbeddingAdapter(cfg)
            result = adapter.embed(["t"])

        assert result == [[0.1]]
        assert caplog.text.count("OpenAI 429") >= 2

    def test_429_exhausted_raises_error_with_masked_key(self):
        cfg = _make_cfg(api_key="sk-test-key", max_retries=2)

        def make_rl():
            e = openai.RateLimitError("rate limit")
            e.response = MagicMock(headers={})
            return e

        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls, \
             patch("embeddings.openai_adapter.time.sleep"):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.embeddings.create.side_effect = [
                make_rl(), make_rl(), make_rl()
            ]
            adapter = OpenAIEmbeddingAdapter(cfg)
            with pytest.raises(OpenAIEmbeddingError) as exc_info:
                adapter.embed(["t"])
        assert "sk-te****" in str(exc_info.value)

    def test_429_respects_retry_after_header(self):
        cfg = _make_cfg(max_retries=1)
        rl_err = openai.RateLimitError("rate limit")
        rl_err.response = MagicMock(headers={"retry-after": "5"})

        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls, \
             patch("embeddings.openai_adapter.time.sleep") as mock_sleep:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.embeddings.create.side_effect = [
                rl_err,
                _make_embedding_response([[0.1]]),
            ]
            adapter = OpenAIEmbeddingAdapter(cfg)
            adapter.embed(["t"])

        # sleep was called; value should be within ±25% jitter of 5.0
        assert mock_sleep.call_count >= 1
        sleep_val = mock_sleep.call_args[0][0]
        assert 5.0 - 1.25 <= sleep_val <= 5.0 + 1.25


# ---------------------------------------------------------------------------
# SC-5: Key masking
# ---------------------------------------------------------------------------

class TestKeyMasking:
    """Verify _mask_key and that raw key never appears in logs or exceptions."""

    def test_mask_key_full(self):
        assert _mask_key("sk-real-secret-key-123") == "sk-re****"

    def test_mask_key_short(self):
        assert _mask_key("abc") == "****"

    def test_mask_key_empty(self):
        assert _mask_key("") == "****"

    def test_raw_key_absent_from_repr(self):
        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            adapter = OpenAIEmbeddingAdapter(_make_cfg(api_key="sk-test-key"))
        rep = repr(adapter)
        assert "sk-test-key" not in rep
        assert "sk-te****" in rep

    def test_raw_key_absent_from_exception(self, caplog):
        cfg = _make_cfg(api_key="sk-real-secret-key-EXACT", max_retries=2)

        def make_rl():
            e = openai.RateLimitError("rate limit")
            e.response = MagicMock(headers={})
            return e

        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls, \
             patch("embeddings.openai_adapter.time.sleep"), \
             caplog.at_level(logging.WARNING, logger="embeddings.openai_adapter"):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.embeddings.create.side_effect = [
                make_rl(), make_rl(), make_rl()
            ]
            adapter = OpenAIEmbeddingAdapter(cfg)
            with pytest.raises(OpenAIEmbeddingError) as exc_info:
                adapter.embed(["t"])

        raw = "sk-real-secret-key-EXACT"
        assert raw not in str(exc_info.value), "Raw key must not appear in exception"
        assert raw not in caplog.text, "Raw key must not appear in log output"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Verify non-429 SDK errors are wrapped in OpenAIEmbeddingError."""

    def test_api_connection_error_wrapped(self):
        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            conn_err = openai.APIConnectionError.__new__(openai.APIConnectionError)
            mock_client.embeddings.create.side_effect = conn_err
            adapter = OpenAIEmbeddingAdapter(_make_cfg())
            with pytest.raises(OpenAIEmbeddingError, match="Cannot connect"):
                adapter.embed(["t"])

    def test_api_status_error_wrapped(self):
        status_err = openai.APIStatusError("Internal server error")
        status_err.status_code = 500

        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.embeddings.create.side_effect = status_err
            adapter = OpenAIEmbeddingAdapter(_make_cfg(api_key="sk-test-key"))
            with pytest.raises(OpenAIEmbeddingError) as exc_info:
                adapter.embed(["t"])
        assert "HTTP 500" in str(exc_info.value)
        assert "sk-test-key" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Empty input guard
# ---------------------------------------------------------------------------

class TestEmptyInput:
    """Verify embed([]) returns [] without any API call."""

    def test_empty_list_returns_empty_and_no_api_call(self):
        with patch("embeddings.openai_adapter.openai.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            adapter = OpenAIEmbeddingAdapter(_make_cfg())
            result = adapter.embed([])
        assert result == []
        assert mock_client.embeddings.create.call_count == 0
