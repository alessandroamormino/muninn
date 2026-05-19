"""OllamaEmbeddingAdapter — calls a local Ollama server for batch text embeddings."""
from __future__ import annotations

import logging
import os

import requests

from embeddings.base import BaseEmbeddingAdapter
from config.settings import EmbeddingConfig

logger = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "http://localhost:11434"


class EmbeddingError(Exception):
    """Raised when Ollama returns an error or is unreachable."""


class OllamaEmbeddingAdapter(BaseEmbeddingAdapter):
    def __init__(self, embedding_cfg: EmbeddingConfig) -> None:
        self._model = embedding_cfg.model
        base = (embedding_cfg.endpoint or os.getenv("OLLAMA_ENDPOINT") or _DEFAULT_ENDPOINT).rstrip("/")
        self._embed_url = f"{base}/api/embed"
        self._cached_dimensions: int | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = requests.post(
                self._embed_url,
                json={"model": self._model, "input": texts},
                timeout=300,
            )
        except requests.exceptions.ConnectionError as exc:
            raise EmbeddingError(
                f"Cannot connect to Ollama at {self._embed_url}: {exc}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise EmbeddingError(f"Ollama request failed: {exc}") from exc

        if not response.ok:
            raise EmbeddingError(
                f"Ollama returned HTTP {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        embeddings = data.get("embeddings")
        if not embeddings:
            raise EmbeddingError(f"Ollama response missing 'embeddings' key: {data}")
        return embeddings

    def dimensions(self) -> int:
        if self._cached_dimensions is None:
            sample = self.embed(["test"])
            self._cached_dimensions = len(sample[0])
        return self._cached_dimensions

    def model_name(self) -> str:
        return self._model
