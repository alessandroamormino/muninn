"""OpenAIEmbeddingAdapter — cloud embedding via OpenAI /v1/embeddings (sync path).

Implements BaseEmbeddingAdapter for ``embedding.type: openai`` in config.yaml.
The sync path (Plan 25-01) calls ``client.embeddings.create()`` directly; the
async Batch API path (Plan 25-02) is gated behind ``embedding.openai_batch: true``
and will be implemented separately.

API key is resolved from ``embedding.api_key`` (supports ``${VAR}`` placeholders)
or from the ``OPENAI_API_KEY`` environment variable as a fallback.
"""
from __future__ import annotations

import logging
import os
import random
import re
import time

import openai

from config.settings import EmbeddingConfig
from embeddings.base import BaseEmbeddingAdapter

logger = logging.getLogger(__name__)

_MODEL_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}
_INITIAL_DELAY = 1.0   # seconds before first retry
_MAX_DELAY = 60.0      # maximum sleep cap in seconds
_JITTER_FACTOR = 0.25  # ±25% jitter on computed delay


class OpenAIEmbeddingError(Exception):
    """Raised when OpenAI returns an error or is unreachable."""


class OpenAIEmbeddingAdapter(BaseEmbeddingAdapter):
    """Embedding adapter that calls OpenAI /v1/embeddings synchronously.

    Supports ``text-embedding-3-small`` (1536 dims) and ``text-embedding-3-large``
    (3072 dims). Unknown models are probed with a single test embedding to obtain
    the dimension count, which is then cached.

    Rate-limit errors (HTTP 429) are retried with exponential backoff + jitter and
    a WARNING log per attempt. The raw API key is never included in any log output
    or exception message — only the masked form (first 5 chars + ``****``) is used.
    """

    def __init__(self, embedding_cfg: EmbeddingConfig) -> None:
        raw_key = _resolve_env_var(embedding_cfg.api_key) or os.getenv("OPENAI_API_KEY")
        if not raw_key:
            raise ValueError(
                "OpenAI API key not found. Set embedding.api_key "
                "(e.g. ${OPENAI_API_KEY}) or set OPENAI_API_KEY env var."
            )
        self._api_key = raw_key
        self._masked_key = _mask_key(raw_key)
        self._model = embedding_cfg.model or "text-embedding-3-small"
        self._max_retries = getattr(embedding_cfg, "max_retries", 5)
        # max_retries=0 on the SDK client: we handle retry ourselves with WARNING logs.
        # SDK silent retries would violate SC-4 (log per retry required).
        self._client = openai.OpenAI(api_key=raw_key, max_retries=0)
        self._cached_dimensions: int | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for ``texts``.

        Returns ``[]`` immediately for an empty input without calling the API.
        """
        if not texts:
            return []
        return _embed_with_retry(
            self._client, texts, self._model, self._max_retries, self._masked_key
        )

    def dimensions(self) -> int:
        """Return the vector dimension for the configured model.

        For known models (text-embedding-3-small, text-embedding-3-large) the
        dimension is returned from the ``_MODEL_DIMS`` lookup without any API
        call. For unknown models, a single probe embedding is made and the result
        is cached to avoid repeated API calls.
        """
        if self._model in _MODEL_DIMS:
            return _MODEL_DIMS[self._model]
        if self._cached_dimensions is None:
            sample = self.embed(["test"])
            self._cached_dimensions = len(sample[0])
        return self._cached_dimensions

    def model_name(self) -> str:
        """Return the model name as configured."""
        return self._model

    def __repr__(self) -> str:
        # Never expose the raw key — only the masked form.
        return f"OpenAIEmbeddingAdapter(model={self._model!r}, key={self._masked_key})"


# ---------------------------------------------------------------------------
# Module-level helpers (not methods so they are independently unit-testable)
# ---------------------------------------------------------------------------

def _mask_key(key: str) -> str:
    """Mask an API key: show first 5 chars + ``****``.

    Examples::

        _mask_key("sk-real-secret-key-123")  # "sk-re****"
        _mask_key("abc")                      # "****"  (too short)
        _mask_key("")                         # "****"
    """
    if not key or len(key) < 6:
        return "****"
    return key[:5] + "****"


def _resolve_env_var(value: str | None) -> str | None:
    """Resolve a ``${VAR}`` placeholder to its environment variable value.

    Returns ``None`` if ``value`` is ``None``.
    Returns the env var value (or ``None`` if unset) when ``value`` matches
    exactly ``${SOME_VAR}``.
    Returns ``value`` unchanged when no placeholder pattern is found.
    """
    if value is None:
        return None
    match = re.fullmatch(r"\$\{([^}]+)\}", value.strip())
    if match:
        return os.getenv(match.group(1))
    return value


def _embed_with_retry(
    client: openai.OpenAI,
    texts: list[str],
    model: str,
    max_retries: int,
    masked_key: str,
) -> list[list[float]]:
    """Call ``client.embeddings.create`` with exponential-backoff retry on 429.

    On ``RateLimitError``, logs WARNING per attempt with masked key and retries
    up to ``max_retries`` times. Other SDK errors are wrapped in
    ``OpenAIEmbeddingError`` and raised immediately (no retry).

    After exhausting all retries, raises ``OpenAIEmbeddingError`` containing the
    masked key (never the raw key).
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.embeddings.create(
                model=model,
                input=texts,
                encoding_format="float",
            )
            return [item.embedding for item in response.data]
        except openai.RateLimitError as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            # Respect Retry-After header if provided by the server.
            retry_after = None
            if hasattr(exc, "response") and exc.response is not None:
                retry_after = getattr(exc.response, "headers", {}).get("retry-after")
            if retry_after:
                delay = float(retry_after)
            else:
                delay = min(_INITIAL_DELAY * (2.0 ** attempt), _MAX_DELAY)
            jitter = delay * _JITTER_FACTOR * (random.random() * 2 - 1)
            sleep_secs = max(0.0, delay + jitter)
            logger.warning(
                "OpenAI 429 RateLimitError — retry %d/%d, sleeping %.1fs (key: %s)",
                attempt + 1,
                max_retries,
                sleep_secs,
                masked_key,
            )
            time.sleep(sleep_secs)
        except openai.APIConnectionError as exc:
            raise OpenAIEmbeddingError(f"Cannot connect to OpenAI: {exc}") from exc
        except openai.APIStatusError as exc:
            raise OpenAIEmbeddingError(
                f"OpenAI API error HTTP {getattr(exc, 'status_code', '?')} (key: {masked_key})"
            ) from exc
    raise OpenAIEmbeddingError(
        f"OpenAI rate limit exceeded after {max_retries} retries (key: {masked_key})"
    ) from last_exc
