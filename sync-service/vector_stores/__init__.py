"""vector_stores package — factory + public exports.

Usage:
    from vector_stores import get_vector_store, BaseVectorStore

The factory reads VECTOR_STORE_ENGINE from environment (default: "weaviate") and
returns the matching implementation. Imports are deferred inside get_vector_store()
to avoid circular imports and to keep each backend's heavy dependencies optional.
"""
from __future__ import annotations

from vector_stores.base import BaseVectorStore

__all__ = ["get_vector_store", "BaseVectorStore"]


def get_vector_store(engine: str, url: str) -> BaseVectorStore:
    """Instantiate and return the correct vector store implementation.

    Args:
        engine: "weaviate" or "qdrant"
        url: vector store URL (e.g. "http://localhost:8080" for Weaviate,
             "http://localhost:6333" for Qdrant)

    Returns:
        Concrete BaseVectorStore instance (not yet open — caller must call .open()).

    Raises:
        ValueError: if engine is not "weaviate" or "qdrant".
    """
    if engine == "weaviate":
        from vector_stores.weaviate_store import WeaviateVectorStore
        return WeaviateVectorStore(url)
    elif engine == "qdrant":
        from vector_stores.qdrant_store import QdrantVectorStore  # type: ignore[import]
        return QdrantVectorStore(url)
    else:
        raise ValueError(f"Unknown engine: {engine!r}. Use 'weaviate' or 'qdrant'.")
