"""BaseVectorStore ABC — engine-agnostic interface for all vector store backends.

All application code (engine.py, search.py, graph.py, main.py) interacts with
the vector store exclusively through this interface. Engine-specific imports are
confined to WeaviateVectorStore and QdrantVectorStore implementations.

Defines:
  - SearchHit: engine-agnostic search result dataclass
  - IndexResult: mirrors existing UpsertResult (inserted/updated/skipped)
  - compute_record_uuid: deterministic UUID5 (engine-agnostic, same for Weaviate and Qdrant)
  - WEAVIATE_MODES / QDRANT_MODES: allowed search_mode frozensets
  - validate_search_mode_compatibility: fail-fast D-04 guard
  - BaseVectorStore: ABC with 10 abstract methods
"""
from __future__ import annotations

import uuid as _uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass
class SearchHit:
    """Engine-agnostic search result returned by BaseVectorStore.search()."""
    properties: dict[str, Any]
    score: float


@dataclass
class IndexResult:
    """Mirrors existing UpsertResult shape. Returned by BaseVectorStore.index_records()."""
    inserted: int
    updated: int
    skipped: int

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.skipped


# ---------------------------------------------------------------------------
# UUID utility (engine-agnostic)
# ---------------------------------------------------------------------------

def compute_record_uuid(source_type: str, record_id: str) -> _uuid.UUID:
    """Deterministic UUID5 per D-11: uuid5(NAMESPACE_DNS, source_type + ':' + record_id).

    Mirrors existing weaviate_store/upsert.py compute_record_uuid exactly.
    Lives here because it is engine-agnostic — both WeaviateVectorStore and
    QdrantVectorStore use the same UUID for the same logical record.
    For Qdrant: str(compute_record_uuid(...)) produces standard UUID format required
    by PointStruct.id (Pitfall 5 in RESEARCH.md).
    """
    if not source_type:
        raise ValueError("source_type must be a non-empty string")
    if not record_id:
        raise ValueError("record_id must be a non-empty string")
    return _uuid.uuid5(_uuid.NAMESPACE_DNS, f"{source_type}:{record_id}")


# ---------------------------------------------------------------------------
# Search mode compatibility (D-04)
# ---------------------------------------------------------------------------

#: Allowed search modes for Weaviate engine.
WEAVIATE_MODES: frozenset[str] = frozenset({"hybrid", "vector", "bm25"})

#: Allowed search modes for Qdrant engine (superset — includes fts).
QDRANT_MODES: frozenset[str] = frozenset({"hybrid", "vector", "bm25", "fts"})


def validate_search_mode_compatibility(engine: str, configs: list) -> None:
    """Fail-fast D-04 guard: raise RuntimeError if any entity config has an incompatible search_mode.

    Called at startup (main.py lifespan) after loading all entity configs.
    RuntimeError message includes the collection name, the offending mode, the engine
    name, and the list of allowed modes — enough context to fix the config.

    Args:
        engine: "weaviate" or "qdrant"
        configs: list of AppConfig objects (or mocks with .weaviate.collection and
                 .weaviate.search_mode attributes)

    Raises:
        RuntimeError: on first incompatible search_mode found.
    """
    allowed = WEAVIATE_MODES if engine == "weaviate" else QDRANT_MODES
    for cfg in configs:
        mode = getattr(cfg.weaviate, "search_mode", "hybrid")
        if mode not in allowed:
            raise RuntimeError(
                f"Entity {cfg.weaviate.collection}: search_mode={mode!r} non supportata "
                f"da {engine}. Modi disponibili: {sorted(allowed)}"
            )


# ---------------------------------------------------------------------------
# BaseVectorStore ABC
# ---------------------------------------------------------------------------

class BaseVectorStore(ABC):
    """Abstract base class for all vector store backends.

    Concrete implementations: WeaviateVectorStore, QdrantVectorStore.
    The factory function get_vector_store() (in __init__.py) returns the correct
    implementation based on the VECTOR_STORE_ENGINE env var.

    Lifecycle:
        open()  — establish connection (idempotent)
        close() — tear down connection (safe to call when not open)

    Index management:
        create_index(cfg)          — create collection/index if missing
        drop_index(collection)     — drop collection unconditionally
        index_exists(collection)   — check if collection exists

    Data operations:
        index_records(...)         — upsert records (handles embedding internally)
        search(...)                — run a query; returns list[SearchHit]
        count(collection)          — total document count

    Graph support:
        get_vectors_for_graph(...) — return vectors for UMAP/HDBSCAN (None if FTS-only)

    Health:
        is_live()                  — True if backend is reachable
    """

    @abstractmethod
    def open(self) -> None:
        """Open connection. Idempotent."""

    @abstractmethod
    def close(self) -> None:
        """Close connection. Safe to call when not open."""

    @abstractmethod
    def create_index(self, cfg: "Any") -> bool:
        """Create collection/index if missing. Returns True if created, False if already existed."""

    @abstractmethod
    def drop_index(self, collection_name: str) -> None:
        """Drop collection/index unconditionally."""

    @abstractmethod
    def index_exists(self, collection_name: str) -> bool:
        """Return True if the collection/index exists."""

    @abstractmethod
    def index_records(
        self,
        records: list[dict[str, Any]],
        cfg: "Any",
        source_type: str,
        embedding_adapter: Any = None,
        id_field: str | None = None,
        start_from_batch: int = 0,
        on_batch_done: Callable[[int, int, int], None] | None = None,
    ) -> IndexResult:
        """Upsert records into the index. Handles embedding internally.

        Args:
            records: list of dicts from the source adapter
            cfg: AppConfig for this entity (provides weaviate.collection, text_fields, etc.)
            source_type: "csv" | "json" | "mysql" | ... (used for UUID generation)
            embedding_adapter: BaseEmbeddingAdapter or None (None = server-side vectorization)
            id_field: field name to use as record ID (overrides cfg default if provided)
            start_from_batch: skip already-processed batches (resumable full re-index)
            on_batch_done: callback(batch_num, done_records, total_records) for progress
        """

    @abstractmethod
    def search(
        self,
        query: str,
        query_vector: list[float] | None,
        cfg: "Any",
        filters: list[tuple[str, str]] | None = None,
        limit: int = 10,
        mode: str = "hybrid",
    ) -> list[SearchHit]:
        """Run a search query. Returns ranked results.

        Args:
            query: raw query string (for BM25/FTS component)
            query_vector: pre-computed embedding or None (None = server-side vectorization)
            cfg: AppConfig (provides collection name, text_fields, etc.)
            filters: list of (campo, valore) pairs; engine applies AND logic
            limit: max results to return
            mode: "hybrid" | "vector" | "bm25" | "fts"
        """

    @abstractmethod
    def count(self, collection_name: str) -> int | None:
        """Return total document count; None on error."""

    @abstractmethod
    def get_vectors_for_graph(
        self,
        collection_name: str,
        max_nodes: int = 2000,
    ) -> list[dict[str, Any]] | None:
        """Return list of {"vector": list[float], "payload": dict} for UMAP/HDBSCAN.

        Returns None if this store/mode does not support vector retrieval
        (e.g., FTS-only collections in Qdrant where no dense vectors are stored).
        Returns None on any error — caller must handle gracefully.
        """

    @abstractmethod
    def is_live(self) -> bool:
        """Health check. Returns True if the backend is reachable."""
