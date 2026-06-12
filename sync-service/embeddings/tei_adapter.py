"""TEIEmbeddingAdapter — calls a HuggingFace Text Embeddings Inference server.

TEI API (native /embed endpoint):
  POST /embed
  Request:  {"inputs": ["text1", "text2"]}   (list) or {"inputs": "text"} (single)
  Response: [[float, ...], [float, ...]]      (raw array of arrays, no wrapper key)

TEI is NOT compatible with Ollama's /api/embed format.
TEI uses Metal natively on Mac Silicon — ~5-10x faster than Ollama for bge-m3.

Installation (Mac):
  brew install text-embeddings-inference

Start server:
  text-embeddings-router --model-id BAAI/bge-m3 --port 8082

Config:
  embedding:
    type: tei
    endpoint: http://host.docker.internal:8082
    model: BAAI/bge-m3
"""
from __future__ import annotations

import logging
import os

import requests

from config.settings import EmbeddingConfig
from embeddings.base import BaseEmbeddingAdapter

logger = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "http://localhost:8082"
# Must match --max-client-batch-size passed to text-embeddings-router.
# Run TEI with: text-embeddings-router --model-id BAAI/bge-m3 --port 8082 --max-client-batch-size 256
_TEI_MAX_BATCH = 256


class TEIEmbeddingError(Exception):
    """Raised when TEI returns an error or is unreachable."""


class TEIEmbeddingAdapter(BaseEmbeddingAdapter):
    def __init__(self, embedding_cfg: EmbeddingConfig) -> None:
        self._model = embedding_cfg.model
        base = (
            embedding_cfg.endpoint
            or os.getenv("TEI_ENDPOINT")
            or _DEFAULT_ENDPOINT
        ).rstrip("/")
        self._embed_url = f"{base}/embed"
        self._cached_dimensions: int | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # TEI enforces a max-client-batch-size (default 32). Split here so callers
        # can pass arbitrarily large lists without restarting TEI with custom flags.
        results: list[list[float]] = []
        for i in range(0, len(texts), _TEI_MAX_BATCH):
            results.extend(self._embed_chunk(texts[i: i + _TEI_MAX_BATCH]))
        return results

    def _embed_chunk(self, texts: list[str]) -> list[list[float]]:
        try:
            response = requests.post(
                self._embed_url,
                json={"inputs": texts},
                timeout=300,
            )
        except requests.exceptions.ConnectionError as exc:
            raise TEIEmbeddingError(
                f"Cannot connect to TEI at {self._embed_url}: {exc}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise TEIEmbeddingError(f"TEI request failed: {exc}") from exc

        if not response.ok:
            raise TEIEmbeddingError(
                f"TEI returned HTTP {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        # TEI /embed returns [[float,...], ...] directly (no wrapper key)
        if not isinstance(data, list):
            raise TEIEmbeddingError(
                f"TEI response expected list, got {type(data).__name__}: {str(data)[:200]}"
            )
        return data

    def dimensions(self) -> int:
        if self._cached_dimensions is None:
            sample = self.embed(["test"])
            self._cached_dimensions = len(sample[0])
        return self._cached_dimensions

    def model_name(self) -> str:
        return self._model
