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
        configs: list of AppConfig objects (or mocks with .vector_store.collection and
                 .vector_store.search_mode attributes)

    Raises:
        RuntimeError: on first incompatible search_mode found.
    """
    allowed = WEAVIATE_MODES if engine == "weaviate" else QDRANT_MODES
    for cfg in configs:
        mode = getattr(cfg.vector_store, "search_mode", "hybrid")
        if mode not in allowed:
            raise RuntimeError(
                f"Entity {cfg.vector_store.collection}: search_mode={mode!r} non supportata "
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

    Snapshot / restore (entity load/unload, Phase 26):
        supports_snapshots()           — True if engine supports snapshot/restore (default: False)
        snapshot_collection(name)      — create snapshot, return its name (default: raise)
        restore_collection(name, snap) — restore from a snapshot (default: raise)

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
        batch_num_offset: int = 0,
        on_batch_done: Callable[[int, int, int], None] | None = None,
        is_full_index: bool = False,
    ) -> IndexResult:
        """Upsert records into the index. Handles embedding internally.

        Args:
            records: list of dicts from the source adapter
            cfg: AppConfig for this entity (provides weaviate.collection, text_fields, etc.)
            source_type: "csv" | "json" | "mysql" | ... (used for UUID generation)
            embedding_adapter: BaseEmbeddingAdapter or None (None = server-side vectorization)
            id_field: field name to use as record ID (overrides cfg default if provided)
            start_from_batch: skip already-processed batches (resumable full re-index)
            batch_num_offset: add this offset to internal batch_num counter so
                on_batch_done reports global batch numbers when called per-chunk
                from the streaming pipeline in run_full().
            on_batch_done: callback(batch_num, done_records, total_records) for progress
            is_full_index: when True, implementations may apply bulk-load optimizations
                (e.g. Qdrant HNSW staging: disables index building during upsert, rebuilds after)
        """

    def begin_bulk_load(self, collection_name: str, mode: str) -> None:
        """Called before a streaming full-index bulk upsert.

        Implementations may apply pre-ingestion optimizations here (e.g. Qdrant:
        disable HNSW index building with m=0 to speed up bulk insert).
        No-op by default — safe for engines that don't need staging.
        """

    def end_bulk_load(self, collection_name: str) -> None:
        """Called after a streaming full-index bulk upsert completes (or fails).

        Implementations restore production settings set in begin_bulk_load
        (e.g. Qdrant: rebuild HNSW with m=16). No-op by default.
        """

    # ------------------------------------------------------------------
    # Snapshot / restore (Phase 26 — entity load/unload management)
    # ------------------------------------------------------------------

    def supports_snapshots(self) -> bool:
        """Return True if this engine supports snapshot/restore (unload/load).

        Default: False. Only QdrantVectorStore overrides this to True (D-06).
        """
        return False

    def snapshot_collection(self, collection_name: str) -> str:
        """Create a snapshot of collection_name and return its name.

        Default: raises NotImplementedError. Only QdrantVectorStore implements this.
        """
        raise NotImplementedError(
            f"{type(self).__name__} non supporta snapshot/unload. "
            "Usa l'engine Qdrant per questa funzionalità."
        )

    def restore_collection(self, collection_name: str, snapshot_name: str) -> None:
        """Restore collection_name from a previously created snapshot.

        Default: raises NotImplementedError. Only QdrantVectorStore implements this.
        """
        raise NotImplementedError(
            f"{type(self).__name__} non supporta snapshot/restore."
        )

    @abstractmethod
    def search(
        self,
        query: str,
        query_vector: list[float] | None,
        cfg: "Any",
        filters: list[tuple[str, str]] | None = None,
        limit: int = 10,
        mode: str = "hybrid",
        must_not_text_terms: list[str] | None = None,
    ) -> list[SearchHit]:
        """Run a search query. Returns ranked results.

        Args:
            query: raw query string (for BM25/FTS component)
            query_vector: pre-computed embedding or None (None = server-side vectorization)
            cfg: AppConfig (provides collection name, text_fields, etc.)
            filters: list of (campo, valore) pairs; engine applies AND logic
            limit: max results to return
            mode: "hybrid" | "vector" | "bm25" | "fts"
            must_not_text_terms: terms that must NOT appear in _fts_text (server-side exclusion).
                For fts/bm25 + negation: Qdrant uses scroll+must_not; Weaviate ignores (post-filter handles it).
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
