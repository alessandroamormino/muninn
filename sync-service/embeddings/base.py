"""BaseEmbeddingAdapter — all embedding adapters must implement this interface."""
from __future__ import annotations

from abc import ABC, abstractmethod


class BaseEmbeddingAdapter(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a list of texts."""

    @abstractmethod
    def dimensions(self) -> int:
        """Return the number of dimensions in the output vectors."""

    @abstractmethod
    def model_name(self) -> str:
        """Return the model name (used to detect model change and trigger re-index)."""
