"""WeaviateVectorStore — BaseVectorStore implementation backed by existing weaviate_store/ modules.

This is a thin wrapper that delegates every method to the corresponding
weaviate_store/* function. The existing weaviate_store/ package is RETAINED
unchanged — this file only adds the abstraction layer on top of it.

Imports inside methods to avoid circular imports and to keep weaviate-client
as an optional runtime dependency (only loaded when engine == "weaviate").
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from vector_stores.base import BaseVectorStore, IndexResult, SearchHit

logger = logging.getLogger(__name__)

# Import top-level names used at module load time (not engine-specific heavy deps)
from weaviate_store.client import open_client, close_client, get_client
from weaviate_store.schema import create_collection_if_missing
from weaviate_store.upsert import upsert_records


class WeaviateVectorStore(BaseVectorStore):
    """Vector store implementation backed by Weaviate v4.

    Delegates all operations to the existing weaviate_store/ modules.
    The search() method extracts the Weaviate-specific hybrid query logic
    that previously lived in api/search.py, making search.py engine-agnostic.
    """

    def __init__(self, url: str) -> None:
        """Initialize with Weaviate URL. Does NOT open the connection."""
        self._url = url

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open Weaviate client connection. Idempotent."""
        open_client()

    def close(self) -> None:
        """Close Weaviate client connection. Safe to call when not open."""
        close_client()

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def create_index(self, cfg: Any) -> bool:
        """Create Weaviate collection if missing. Returns True if created."""
        client = get_client()
        dims: int | None = None
        if hasattr(cfg, "embedding") and cfg.embedding is not None:
            # Get dims from embedding config if available (for PQ quantization segments)
            from embeddings import build_embedding_adapter
            try:
                adapter = build_embedding_adapter(cfg.embedding)
                if adapter is not None:
                    dims = adapter.dimensions()
            except Exception:  # noqa: BLE001
                pass
        return create_collection_if_missing(
            client,
            cfg.weaviate,
            embedding_type=cfg.embedding.type,
            embedding_dims=dims,
        )

    def drop_index(self, collection_name: str) -> None:
        """Drop Weaviate collection unconditionally (no-op if missing)."""
        client = get_client()
        if client.collections.exists(collection_name):
            client.collections.delete(collection_name)

    def index_exists(self, collection_name: str) -> bool:
        """Return True if the Weaviate collection exists."""
        return get_client().collections.exists(collection_name)

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    def index_records(
        self,
        records: list[dict[str, Any]],
        cfg: Any,
        source_type: str,
        embedding_adapter: Any = None,
        id_field: str | None = None,
        start_from_batch: int = 0,
        on_batch_done: Callable[[int, int, int], None] | None = None,
    ) -> IndexResult:
        """Upsert records via weaviate_store.upsert.upsert_records(). Returns IndexResult."""
        client = get_client()
        r = upsert_records(
            client,
            records,
            cfg.weaviate,
            source_type,
            embedding_adapter,
            id_field=id_field,
            start_from_batch=start_from_batch,
            on_batch_done=on_batch_done,
        )
        return IndexResult(inserted=r.inserted, updated=r.updated, skipped=r.skipped)

    def search(
        self,
        query: str,
        query_vector: list[float] | None,
        cfg: Any,
        filters: list[tuple[str, str]] | None = None,
        limit: int = 10,
        mode: str = "hybrid",
    ) -> list[SearchHit]:
        """Execute Weaviate hybrid/vector/bm25 query. Returns list[SearchHit].

        Builds Weaviate-specific filter objects and runs the hybrid query.
        The Weaviate first-char lowercase transform for property names lives HERE
        (not in search.py) so search.py is engine-agnostic.

        alpha mapping:
          hybrid -> 0.5 (balanced BM25 + vector)
          vector -> 1.0 (vector only)
          bm25   -> 0.0 (BM25 only, uses existing hybrid(alpha=0.0) pattern)
        """
        import weaviate.classes.query as _wvc_query

        client = get_client()
        collection_obj = client.collections.get(cfg.vector_store.collection)

        # Build Weaviate filter from engine-agnostic (campo, valore) pairs.
        # Weaviate lowercases the first char of every property at schema creation time.
        weaviate_filter = None
        if filters:
            parsed = []
            for campo, valore in filters:
                weaviate_campo = campo[0].lower() + campo[1:] if campo else campo
                parsed.append(_wvc_query.Filter.by_property(weaviate_campo).like(valore))
            if parsed:
                weaviate_filter = parsed[0]
                for f in parsed[1:]:
                    weaviate_filter = weaviate_filter & f

        # Map mode to alpha (Weaviate uses alpha to blend BM25 and vector).
        _alpha_map = {"hybrid": 0.5, "vector": 1.0, "bm25": 0.0}
        alpha = _alpha_map.get(mode, 0.5)

        if query_vector is not None:
            results = collection_obj.query.hybrid(
                query=query,
                vector=query_vector,
                alpha=alpha,
                limit=limit,
                return_metadata=_wvc_query.MetadataQuery(score=True),
                filters=weaviate_filter,
            )
        else:
            results = collection_obj.query.hybrid(
                query=query,
                alpha=alpha,
                limit=limit,
                return_metadata=_wvc_query.MetadataQuery(score=True),
                filters=weaviate_filter,
            )

        return [
            SearchHit(
                properties=dict(obj.properties),
                score=obj.metadata.score,
            )
            for obj in results.objects
        ]

    def count(self, collection_name: str) -> int | None:
        """Return total document count in the Weaviate collection. None on error."""
        try:
            agg = (
                get_client()
                .collections.get(collection_name)
                .aggregate.over_all(total_count=True)
            )
            return agg.total_count
        except Exception:  # noqa: BLE001
            return None

    def get_vectors_for_graph(
        self,
        collection_name: str,
        max_nodes: int = 2000,
    ) -> list[dict[str, Any]] | None:
        """Return list of {"vector": ..., "payload": ...} for UMAP/HDBSCAN.

        Scrolls the Weaviate collection with include_vector=True. Handles both
        named vectors ({"default": [...]} or {first_key: [...]}) and legacy
        single-vector collections.

        Returns None if no vectors can be retrieved (e.g. empty collection or error).
        """
        try:
            client = get_client()
            col = client.collections.get(collection_name)
            raw_points: list[dict[str, Any]] = []
            for obj in col.iterator(include_vector=True):
                raw_vec = None
                if isinstance(obj.vector, dict):
                    raw_vec = obj.vector.get("default")
                    if raw_vec is None and obj.vector:
                        raw_vec = next(iter(obj.vector.values()))
                if raw_vec is None:
                    continue
                raw_points.append({
                    "vector": raw_vec,
                    "payload": {"id": str(obj.uuid), **dict(obj.properties)},
                })
                if len(raw_points) >= max_nodes:
                    break
            return raw_points if raw_points else None
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def is_live(self) -> bool:
        """Return True if the Weaviate backend is reachable."""
        try:
            return get_client().is_live()
        except Exception:  # noqa: BLE001
            return False
