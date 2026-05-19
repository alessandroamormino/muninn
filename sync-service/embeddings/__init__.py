"""Embedding adapters package."""
from __future__ import annotations

from embeddings.base import BaseEmbeddingAdapter
from embeddings.weaviate_builtin import WeaviateBuiltinAdapter
from embeddings.ollama_adapter import OllamaEmbeddingAdapter


def build_embedding_adapter(embedding_cfg) -> BaseEmbeddingAdapter | None:
    """Factory: return the correct adapter for embedding_cfg.type, or None for weaviate_builtin."""
    if embedding_cfg.type == "ollama":
        return OllamaEmbeddingAdapter(embedding_cfg)
    # weaviate_builtin delegates vectorization to Weaviate server-side — no adapter needed
    return None


__all__ = ["WeaviateBuiltinAdapter", "OllamaEmbeddingAdapter", "build_embedding_adapter"]
