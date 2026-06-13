"""OpenAIEmbeddingAdapter — cloud embedding via OpenAI /v1/embeddings.

Implements BaseEmbeddingAdapter for ``embedding.type: openai`` in config.yaml.

Sync path (Plan 25-01, default):
    Calls ``client.embeddings.create()`` directly with exponential-backoff retry.
    Activated when ``embedding.openai_batch: false`` (default).

Batch API path (Plan 25-02):
    Builds a JSONL file, uploads to /v1/files, submits a batch job to /v1/batches,
    polls until completion, downloads output, parses and reorders by custom_id.
    Activated when ``embedding.openai_batch: true``.
    Checkpoint: batch_id written to .sync/{collection}.batch_checkpoint.json before
    polling so a process restart can resume instead of re-submitting.

API key is resolved from ``embedding.api_key`` (supports ``${VAR}`` placeholders)
or from the ``OPENAI_API_KEY`` environment variable as a fallback.
"""
from __future__ import annotations

import io
import json
import logging
import os
import pathlib
import random
import re
import time
from datetime import datetime, timezone
from typing import Any

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

# Batch API constants (Plan 25-02)
_BATCH_POLL_INTERVAL = 30.0          # seconds between polls
_BATCH_MAX_INPUTS = 50_000           # OpenAI Batch API per-request cap
_BATCH_CHECKPOINT_DIR = pathlib.Path("/app/.sync")  # same root as sync/checkpoint.py
_BATCH_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "expired", "cancelled"}
)


class OpenAIEmbeddingError(Exception):
    """Raised when OpenAI returns an error or is unreachable."""


class OpenAIEmbeddingAdapter(BaseEmbeddingAdapter):
    """Embedding adapter that calls OpenAI /v1/embeddings.

    Sync path (default): calls ``client.embeddings.create()`` per batch with
    exponential-backoff retry on HTTP 429. Activated when
    ``embedding.openai_batch: false`` (or unset).

    Batch API path: submits all texts as a single JSONL batch job, polls until
    completion, downloads and reorders results. Activated when
    ``embedding.openai_batch: true``. See ``embed_batch_async()``.

    Supports ``text-embedding-3-small`` (1536 dims) and ``text-embedding-3-large``
    (3072 dims). Unknown models are probed with a single test embedding to obtain
    the dimension count, which is then cached.

    The raw API key is never included in any log output or exception message —
    only the masked form (first 5 chars + ``****``) is used.
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
        # Store the full config for supports_batch_api property (Plan 25-02).
        self._cfg = embedding_cfg

    # ------------------------------------------------------------------
    # BaseEmbeddingAdapter interface
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Batch API path (Plan 25-02)
    # ------------------------------------------------------------------

    @property
    def supports_batch_api(self) -> bool:
        """Return True when ``embedding.openai_batch: true`` is set in config."""
        return bool(getattr(self._cfg, "openai_batch", False))

    def embed_batch_async(
        self,
        texts: list[str],
        *,
        collection_name: str,
    ) -> list[list[float]]:
        """Submit all texts to OpenAI Batch API, poll until completion, return vectors.

        Full flow:
          1. Empty input → return [] immediately.
          2. Validate len(texts) <= 50,000 (OpenAI Batch API per-request cap).
          3. Check for an existing checkpoint — if found, resume polling that batch_id
             instead of re-uploading (handles process restarts, Pitfall 7).
          4. Build JSONL (one line per text, custom_id = str(index)).
          5. Upload via client.files.create(purpose="batch").
          6. Submit via client.batches.create(endpoint="/v1/embeddings", window="24h").
          7. Write checkpoint (batch_id + file_id) to .sync/{collection}.batch_checkpoint.json
             BEFORE polling so restart can resume.
          8. Poll every 30s (max 2880 iterations = 24h wall-clock cap).
          9. On non-"completed" terminal status → raise OpenAIEmbeddingError.
          10. Download output file, parse JSONL, reorder by custom_id.
          11. Delete checkpoint on success.

        The raw API key NEVER appears in any log or exception — only self._masked_key.

        Args:
            texts: list of texts to embed (all uploaded in one batch job).
            collection_name: used as checkpoint file name prefix.

        Returns:
            list[list[float]] in the same order as the input ``texts``.

        Raises:
            OpenAIEmbeddingError: on terminal failure, expiry, cancellation,
                count mismatch, or exceeding the 50K input cap.
        """
        if not texts:
            return []

        n = len(texts)
        if n > _BATCH_MAX_INPUTS:
            raise OpenAIEmbeddingError(
                f"Batch API supports max 50,000 inputs per request; got {n}. "
                f"Use sync path (openai_batch: false) or split the dataset. "
                f"(key: {self._masked_key})"
            )

        # --- Resume: check for existing checkpoint ---
        ckpt = _read_batch_checkpoint(collection_name)
        if ckpt is not None:
            batch_id = ckpt["batch_id"]
            logger.warning(
                "Resuming poll of existing batch %s from checkpoint (submitted_at=%s)",
                batch_id,
                ckpt.get("submitted_at", "unknown"),
            )
            # Check immediately whether it's still alive
            batch = self._client.batches.retrieve(batch_id)
            if batch.status in _BATCH_TERMINAL_STATUSES:
                if batch.status == "expired":
                    raise OpenAIEmbeddingError(
                        f"OpenAI batch {batch_id} has expired. Delete the checkpoint file "
                        f"at {_BATCH_CHECKPOINT_DIR / (collection_name + '.batch_checkpoint.json')} "
                        f"and retry. (key: {self._masked_key})"
                    )
                if batch.status != "completed":
                    raise OpenAIEmbeddingError(
                        f"OpenAI batch {batch_id} ended with status={batch.status} "
                        f"(key: {self._masked_key})"
                    )
                # Already completed — skip polling, go straight to download
            else:
                # Continue polling
                batch = _poll_batch(self._client, batch_id, self._masked_key)
        else:
            # --- Fresh submission ---
            jsonl_bytes = _build_jsonl(texts, self._model)

            file_obj = self._client.files.create(
                file=io.BytesIO(jsonl_bytes),
                purpose="batch",
            )

            batch = self._client.batches.create(
                input_file_id=file_obj.id,
                endpoint="/v1/embeddings",
                completion_window="24h",
            )

            logger.info(
                "OpenAI batch submitted (batch_id=%s, file_id=%s, n_inputs=%d, key=%s)",
                batch.id,
                file_obj.id,
                n,
                self._masked_key,
            )

            # Write checkpoint BEFORE polling so a restart can resume.
            _write_batch_checkpoint(collection_name, batch.id, file_obj.id)

            # Poll until terminal
            batch = _poll_batch(self._client, batch.id, self._masked_key)

        # --- Handle terminal status ---
        if batch.status != "completed":
            raise OpenAIEmbeddingError(
                f"OpenAI batch {batch.id} ended with status={batch.status} "
                f"(key: {self._masked_key})"
            )

        # --- Download and parse output ---
        if not batch.output_file_id:
            raise OpenAIEmbeddingError(
                f"OpenAI batch {batch.id} completed but output_file_id is None "
                f"(transient finalization delay — retry later, key: {self._masked_key})"
            )
        content = self._client.files.content(batch.output_file_id)
        vectors = _parse_batch_output(content, n)

        logger.info(
            "OpenAI batch %s completed; downloaded %d embeddings",
            batch.id,
            len(vectors),
        )

        _delete_batch_checkpoint(collection_name)
        return vectors


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


# ---------------------------------------------------------------------------
# Batch API helpers (Plan 25-02)
# ---------------------------------------------------------------------------

def _build_jsonl(texts: list[str], model: str) -> bytes:
    """Build a JSONL bytes payload for the OpenAI Batch API.

    Each line contains one embedding request with ``custom_id = str(index)``
    so the output can be reordered back to the original input order.

    Args:
        texts: list of texts to embed.
        model: OpenAI model name (e.g. "text-embedding-3-small").

    Returns:
        UTF-8 encoded JSONL bytes, one JSON object per line.
    """
    lines = []
    for i, text in enumerate(texts):
        obj = {
            "custom_id": str(i),
            "method": "POST",
            "url": "/v1/embeddings",
            "body": {
                "model": model,
                "input": text,
                "encoding_format": "float",
            },
        }
        lines.append(json.dumps(obj, ensure_ascii=False))
    return "\n".join(lines).encode("utf-8")


def _parse_batch_output(
    content_text: Any,
    n_inputs: int,
) -> list[list[float]]:
    """Parse the JSONL output file from a completed OpenAI Batch API job.

    Handles two input forms:
    - A plain ``str`` (JSONL content directly).
    - An object with a ``.text`` attribute (like the SDK's ``HttpxBinaryResponseContent``).

    Each line has the structure::

        {"custom_id": "N", "response": {"body": {"data": [{"embedding": [...]}]}}}

    Results are reordered by ``custom_id`` (parsed as int) so the output matches
    the original input order regardless of the order the Batch API returned results.

    Args:
        content_text: JSONL string or SDK content object.
        n_inputs: expected number of results (used for validation).

    Returns:
        list[list[float]] in original input order.

    Raises:
        OpenAIEmbeddingError: if the result count does not match ``n_inputs``
            or if a custom_id is missing from the output.
    """
    # Handle both SDK content object (has .text) and plain string
    if hasattr(content_text, "text"):
        raw = content_text.text
    else:
        raw = content_text

    results_by_id: dict[str, list[float]] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        custom_id = obj["custom_id"]
        # Pitfall 5: path is response.body.data[0].embedding (NOT body.data)
        embedding = obj["response"]["body"]["data"][0]["embedding"]
        results_by_id[custom_id] = embedding

    if len(results_by_id) != n_inputs:
        raise OpenAIEmbeddingError(
            f"Batch output count mismatch: expected {n_inputs}, got {len(results_by_id)}"
        )

    try:
        return [results_by_id[str(i)] for i in range(n_inputs)]
    except KeyError as exc:
        raise OpenAIEmbeddingError(
            f"Batch output missing custom_id {exc}: cannot reorder results"
        ) from exc


def _write_batch_checkpoint(
    collection_name: str,
    batch_id: str,
    input_file_id: str,
) -> None:
    """Write batch_id and file_id to a checkpoint file before polling.

    This allows a process restart to resume polling the existing batch instead
    of re-submitting (avoids redundant API calls and cost, handles Pitfall 7).

    File: ``{_BATCH_CHECKPOINT_DIR}/{collection_name}.batch_checkpoint.json``
    """
    _BATCH_CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    checkpoint_path = _BATCH_CHECKPOINT_DIR / f"{collection_name}.batch_checkpoint.json"
    data = {
        "batch_id": batch_id,
        "input_file_id": input_file_id,
        "submitted_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    checkpoint_path.write_text(json.dumps(data), encoding="utf-8")


def _read_batch_checkpoint(collection_name: str) -> dict | None:
    """Read an existing batch checkpoint file, or return None if not found."""
    checkpoint_path = _BATCH_CHECKPOINT_DIR / f"{collection_name}.batch_checkpoint.json"
    if not checkpoint_path.exists():
        return None
    try:
        return json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _delete_batch_checkpoint(collection_name: str) -> None:
    """Delete the batch checkpoint file after successful completion."""
    checkpoint_path = _BATCH_CHECKPOINT_DIR / f"{collection_name}.batch_checkpoint.json"
    try:
        checkpoint_path.unlink(missing_ok=True)
    except OSError:
        pass


def _poll_batch(
    client: Any,
    batch_id: str,
    masked_key: str,
    sleep_fn: Any = time.sleep,
) -> Any:
    """Poll a batch job until it reaches a terminal status.

    Polls every ``_BATCH_POLL_INTERVAL`` seconds (injected ``sleep_fn`` for tests).
    Maximum 2880 iterations = 24h wall-clock cap (T-25-02-04 mitigation).

    Args:
        client: OpenAI client instance.
        batch_id: the batch job ID to poll.
        masked_key: masked API key for use in error messages only.
        sleep_fn: callable(seconds) — injected for tests to avoid actual sleeping.

    Returns:
        The final batch object (status in ``_BATCH_TERMINAL_STATUSES``).

    Raises:
        OpenAIEmbeddingError: if the batch does not reach terminal status within
            24h (2880 * 30s iterations).
    """
    max_iterations = 2880  # 24h / 30s
    for _ in range(max_iterations):
        batch = client.batches.retrieve(batch_id)
        logger.info(
            "OpenAI batch %s status=%s (%d/%d completed)",
            batch_id,
            batch.status,
            batch.request_counts.completed,
            batch.request_counts.total,
        )
        if batch.status in _BATCH_TERMINAL_STATUSES:
            return batch
        sleep_fn(_BATCH_POLL_INTERVAL)

    raise OpenAIEmbeddingError(
        f"OpenAI batch {batch_id} did not reach terminal status within 24h "
        f"(key: {masked_key})"
    )
