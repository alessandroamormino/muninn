"""WeaviateBuiltinAdapter — delegates embedding to text2vec-transformers sidecar via HTTP."""
from __future__ import annotations

import logging
import os

import requests

from embeddings.base import BaseEmbeddingAdapter
from config.settings import EmbeddingConfig

logger = logging.getLogger(__name__)

_DEFAULT_SIDECAR_URL = "http://localhost:8080"


class EmbeddingError(Exception):
    """Raised when the embedding sidecar returns an error or is unreachable."""


class WeaviateBuiltinAdapter(BaseEmbeddingAdapter):
    def __init__(self, embedding_cfg: EmbeddingConfig) -> None:
        self._model = embedding_cfg.model
        sidecar_base = os.getenv("TRANSFORMERS_INFERENCE_API", _DEFAULT_SIDECAR_URL)
        self._endpoint = f"{sidecar_base.rstrip('/')}/vectors"
        self._cached_dimensions: int | None = None

    def _post_single(self, text: str) -> list[float]:
        """POST a single text to the sidecar and return its vector."""
        try:
            response = requests.post(
                self._endpoint,
                json={"text": text},
                timeout=30,
            )
        except requests.exceptions.ConnectionError as exc:
            raise EmbeddingError(
                f"Cannot connect to text2vec-transformers sidecar at {self._endpoint}: {exc}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise EmbeddingError(
                f"Request to embedding sidecar failed: {exc}"
            ) from exc

        if not response.ok:
            raise EmbeddingError(
                f"Embedding sidecar returned HTTP {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        return data["vector"]

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate vectors for each text by calling the sidecar once per text."""
        if not texts:
            return []
        vectors = [self._post_single(text) for text in texts]
        return vectors

    def dimensions(self) -> int:
        """Return vector dimensionality; cached after first call."""
        if self._cached_dimensions is None:
            sample = self._post_single("test")
            self._cached_dimensions = len(sample)
        return self._cached_dimensions

    def model_name(self) -> str:
        return self._model
