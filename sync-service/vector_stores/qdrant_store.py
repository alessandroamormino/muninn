"""QdrantVectorStore — full BaseVectorStore implementation for Qdrant.

Engine: VECTOR_STORE_ENGINE=qdrant
URL format: http://localhost:6333

Search modes supported (QDRANT_MODES = hybrid | vector | bm25 | fts):
  - hybrid: dense KNN + sparse BM25 via RRF fusion (Prefetch + FusionQuery)
  - vector: dense KNN only (Ollama embeddings required)
  - bm25:   sparse BM25 only (Qdrant native server-side inference)
  - fts:    same as bm25 for scoring; text payload index adds stemming quality
             (PITFALL 1: MatchText filter does NOT produce ranked results — always use sparse BM25 query)

Qdrant collection schema per search_mode:
  - hybrid/vector: vectors_config={"dense": VectorParams(size=dims, distance=COSINE)}
  - hybrid/bm25/fts: sparse_vectors_config={"sparse": SparseVectorParams(modifier=IDF)}
  - hybrid/bm25/fts: create_payload_index("_fts_text", TextIndexParams(stemmer=Snowball))

_fts_text payload: always stored; contains joined text_fields values.

UUID format: str(uuid5(NAMESPACE_DNS, source_type + ":" + record_id))
(Pitfall 5: Qdrant requires standard UUID string format for PointStruct.id)

Filter note: Qdrant does NOT lowercase first char of field names (unlike Weaviate).
Use campo as-is from parsed_filter_pairs.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from qdrant_client import QdrantClient
from qdrant_client import models as qmodels

from vector_stores.base import BaseVectorStore, IndexResult, SearchHit, compute_record_uuid

logger = logging.getLogger(__name__)

_EMBED_BATCH_SIZE = 1000  # mirrors weaviate_store/upsert.py constant

# Maps ISO-639-1 lang codes to Qdrant SnowballLanguage enum attribute names
_SNOWBALL_MAP: dict[str, str] = {
    "en": "ENGLISH",
    "it": "ITALIAN",
    "de": "GERMAN",
    "fr": "FRENCH",
    "es": "SPANISH",
    "pt": "PORTUGUESE",
    "nl": "DUTCH",
    "ru": "RUSSIAN",
    "sv": "SWEDISH",
    "fi": "FINNISH",
    "da": "DANISH",
    "no": "NORWEGIAN",
    "hu": "HUNGARIAN",
    "ro": "ROMANIAN",
    "tr": "TURKISH",
    "ar": "ARABIC",
}


def _get_fts_language(cfg: Any) -> str:
    """Extract FTS stemmer language from cfg.weaviate.fts.language (default: 'en')."""
    try:
        return cfg.weaviate.fts.language or "en"
    except AttributeError:
        return "en"


def _snowball_language(lang: str) -> Any:
    """Map lang code to qdrant_client.models.SnowballLanguage enum value."""
    attr = _SNOWBALL_MAP.get(lang.lower(), "ENGLISH")
    return getattr(qmodels.SnowballLanguage, attr)


class QdrantVectorStore(BaseVectorStore):
    """Qdrant implementation of BaseVectorStore.

    Constructor takes the Qdrant URL (e.g. "http://localhost:6333").
    Call open() before any operation; close() when done.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Any = None  # QdrantClient | None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open connection to Qdrant."""
        self._client = QdrantClient(url=self._url)
        logger.info("QdrantVectorStore: connected to %s", self._url)

    def close(self) -> None:
        """Close connection. Safe to call when not open."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    def is_live(self) -> bool:
        """Health check — True if Qdrant responds to get_collections."""
        try:
            self._client.get_collections()
            return True
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def index_exists(self, collection_name: str) -> bool:
        """Return True if the Qdrant collection exists."""
        return self._client.collection_exists(collection_name)

    def create_index(self, cfg: Any) -> bool:
        """Create Qdrant collection with schema appropriate for search_mode.

        Returns True if created, False if already existed.
        Also persists current search_mode to search_mode_state.json (D-09 support).
        """
        from vector_stores.search_mode_state import write_stored_search_mode

        collection = cfg.weaviate.collection
        mode = getattr(cfg.weaviate, "search_mode", "hybrid")

        if self._client.collection_exists(collection):
            return False

        # Build vectors_config (dense, for hybrid/vector modes)
        vectors_cfg: dict = {}
        if mode in ("hybrid", "vector"):
            # dims from config _embedding_dims if available, else default to 2560 (qwen3-embedding:4b)
            dims = getattr(cfg.weaviate, "_embedding_dims", None) or 2560
            vectors_cfg["dense"] = qmodels.VectorParams(
                size=dims,
                distance=qmodels.Distance.COSINE,
            )

        # Build sparse_vectors_config (BM25 sparse, for hybrid/bm25/fts modes)
        sparse_cfg: dict | None = None
        if mode in ("hybrid", "bm25", "fts"):
            sparse_cfg = {
                "sparse": qmodels.SparseVectorParams(modifier=qmodels.Modifier.IDF)
            }

        self._client.create_collection(
            collection_name=collection,
            vectors_config=vectors_cfg,
            sparse_vectors_config=sparse_cfg,
        )
        logger.info(
            "QdrantVectorStore: created collection %r (mode=%r, dims=%s)",
            collection, mode, dims if mode in ("hybrid", "vector") else "n/a",
        )

        # Create _fts_text payload index for stemming support (hybrid, bm25, fts)
        if mode in ("hybrid", "bm25", "fts"):
            lang = _get_fts_language(cfg)
            snowball_lang = _snowball_language(lang)
            self._client.create_payload_index(
                collection_name=collection,
                field_name="_fts_text",
                field_schema=qmodels.TextIndexParams(
                    type=qmodels.TextIndexType.TEXT,
                    tokenizer=qmodels.TokenizerType.WORD,
                    stemmer=qmodels.SnowballParams(
                        type=qmodels.Snowball.SNOWBALL,
                        language=snowball_lang,
                    ),
                    lowercase=True,
                ),
            )
            logger.info(
                "QdrantVectorStore: created _fts_text payload index (lang=%r, snowball=%r)",
                lang, snowball_lang,
            )

        # Persist current search_mode for D-09 change detection on next startup
        try:
            write_stored_search_mode(collection, mode)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not write search_mode_state for %r: %s", collection, exc)

        return True

    def drop_index(self, collection_name: str) -> None:
        """Drop the Qdrant collection if it exists."""
        if self._client.collection_exists(collection_name):
            logger.info("QdrantVectorStore: dropping collection %r", collection_name)
            self._client.delete_collection(collection_name)
        else:
            logger.info("QdrantVectorStore: collection %r does not exist; nothing to drop.", collection_name)

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
        """Upsert records into Qdrant collection.

        - fts mode: skips embedding entirely (D-08)
        - other modes: computes dense embeddings in batches with dedup (mirrors weaviate upsert.py)
        - Always stores _fts_text payload field (joined text_fields)
        - UUID deterministic: str(uuid5(NAMESPACE_DNS, source_type:record_id))
        """
        mode = getattr(cfg.weaviate, "search_mode", "hybrid")
        if id_field is None:
            id_field = cfg.source.id_field
        collection = cfg.weaviate.collection
        text_fields: list[str] = cfg.weaviate.text_fields or []

        # --- Embedding (skip for fts mode, D-08) ---
        if mode != "fts" and embedding_adapter is not None:
            all_docs = [
                " ".join(str(r.get(f, "")) for f in text_fields if r.get(f))
                for r in records
            ]
            unique_docs = list(dict.fromkeys(all_docs))
            vec_map: dict[str, list[float]] = {}
            num_batches = (len(unique_docs) + _EMBED_BATCH_SIZE - 1) // _EMBED_BATCH_SIZE
            if len(unique_docs) < len(all_docs):
                logger.info(
                    "Embedding dedup: %d docs → %d unique", len(all_docs), len(unique_docs)
                )
            for bn, i in enumerate(range(0, len(unique_docs), _EMBED_BATCH_SIZE)):
                if bn < start_from_batch:
                    continue
                batch_texts = unique_docs[i: i + _EMBED_BATCH_SIZE]
                batch_vecs = embedding_adapter.embed(batch_texts)
                for txt, vec in zip(batch_texts, batch_vecs):
                    vec_map[txt] = vec
                if on_batch_done:
                    on_batch_done(bn, min(i + _EMBED_BATCH_SIZE, len(unique_docs)), len(unique_docs))
            all_vecs: list[list[float] | None] = [vec_map.get(d) for d in all_docs]
        else:
            all_vecs = [None] * len(records)

        # --- Build and upsert points ---
        inserted = 0
        points_batch: list = []

        for j, record in enumerate(records):
            raw_id = record.get(id_field)
            if raw_id is None or raw_id == "":
                continue

            obj_uuid = str(compute_record_uuid(source_type, str(raw_id)))

            # Payload: all non-null/non-empty record fields + _fts_text
            payload = {k: v for k, v in record.items() if v is not None and v != ""}
            fts_text = " ".join(
                str(record.get(f, "")) for f in text_fields if record.get(f)
            )
            payload["_fts_text"] = fts_text

            # Vector dict per mode
            vector: dict = {}
            if mode in ("hybrid", "vector") and all_vecs[j] is not None:
                vector["dense"] = all_vecs[j]
            if mode in ("hybrid", "bm25", "fts"):
                # Server-side BM25 inference via models.Document (no fastembed needed)
                vector["sparse"] = qmodels.Document(text=fts_text, model="Qdrant/bm25")

            points_batch.append(
                qmodels.PointStruct(id=obj_uuid, payload=payload, vector=vector)
            )

            if len(points_batch) >= _EMBED_BATCH_SIZE:
                self._client.upsert(collection_name=collection, points=points_batch)
                inserted += len(points_batch)
                points_batch = []

        if points_batch:
            self._client.upsert(collection_name=collection, points=points_batch)
            inserted += len(points_batch)

        logger.info(
            "QdrantVectorStore.index_records: upserted %d points to %r (mode=%r)",
            inserted, collection, mode,
        )
        return IndexResult(inserted=inserted, updated=0, skipped=0)

    def search(
        self,
        query: str,
        query_vector: list[float] | None,
        cfg: Any,
        filters: list[tuple[str, str]] | None = None,
        limit: int = 10,
        mode: str = "hybrid",
    ) -> list[SearchHit]:
        """Execute search using the appropriate Qdrant query API for search_mode.

        - hybrid: RRF fusion of sparse BM25 + dense KNN (Prefetch + FusionQuery)
        - vector: dense KNN only
        - bm25:   sparse BM25 only via models.Document
        - fts:    SAME as bm25 — sparse BM25 query (NOT MatchText filter — PITFALL 1)

        Filters: FieldCondition with MatchValue. Qdrant does NOT lowercase field names
        (unlike Weaviate). Use campo as-is.
        """
        collection = cfg.weaviate.collection

        # Build filter
        qdrant_filter = None
        if filters:
            conditions = [
                qmodels.FieldCondition(key=campo, match=qmodels.MatchValue(value=valore))
                for campo, valore in filters
            ]
            # NOTE: Qdrant does NOT lowercase first char (unlike Weaviate) — campo used as-is
            qdrant_filter = qmodels.Filter(must=conditions)

        if mode == "hybrid":
            results = self._client.query_points(
                collection_name=collection,
                prefetch=[
                    qmodels.Prefetch(
                        query=qmodels.Document(text=query, model="Qdrant/bm25"),
                        using="sparse",
                        limit=limit * 2,
                    ),
                    qmodels.Prefetch(
                        query=query_vector,
                        using="dense",
                        limit=limit * 2,
                    ),
                ],
                query=qmodels.FusionQuery(fusion=qmodels.Fusion.RRF),
                limit=limit,
                with_payload=True,
                query_filter=qdrant_filter,
            )
        elif mode == "vector":
            results = self._client.query_points(
                collection_name=collection,
                query=query_vector,
                using="dense",
                limit=limit,
                with_payload=True,
                query_filter=qdrant_filter,
            )
        elif mode in ("bm25", "fts"):
            # CRITICAL: Use sparse BM25 query — NOT MatchText filter (PITFALL 1).
            # MatchText as a query_filter does NOT produce relevance scores.
            # BM25 sparse vector query via models.Document returns ranked results.
            results = self._client.query_points(
                collection_name=collection,
                query=qmodels.Document(text=query, model="Qdrant/bm25"),
                using="sparse",
                limit=limit,
                with_payload=True,
                query_filter=qdrant_filter,
            )
        else:
            raise ValueError(f"Unknown search mode: {mode!r}. Supported: hybrid, vector, bm25, fts")

        return [SearchHit(properties=p.payload, score=p.score) for p in results.points]

    def count(self, collection_name: str) -> int | None:
        """Return total document count; None on error."""
        try:
            return self._client.count(collection_name=collection_name, exact=True).count
        except Exception:  # noqa: BLE001
            return None

    def get_vectors_for_graph(
        self,
        collection_name: str,
        max_nodes: int = 2000,
    ) -> list[dict[str, Any]] | None:
        """Return [{vector: list[float], payload: dict}] for UMAP/HDBSCAN.

        Returns None when:
        - Collection has no dense vectors (fts-only mode, D-10)
        - Any error occurs

        Per D-10: caller (api/graph.py) must show the FTS-disabled message when None returned.
        """
        try:
            info = self._client.get_collection(collection_name)
            # Check if dense vector exists in the collection config
            vectors = info.config.params.vectors
            has_dense = isinstance(vectors, dict) and "dense" in vectors
            if not has_dense:
                logger.info(
                    "QdrantVectorStore: collection %r has no dense vectors (fts mode) — graph unavailable (D-10).",
                    collection_name,
                )
                return None
        except Exception:  # noqa: BLE001
            return None

        all_points: list = []
        offset = None
        while True:
            try:
                batch, offset = self._client.scroll(
                    collection_name=collection_name,
                    limit=100,
                    with_payload=True,
                    with_vectors=["dense"],
                    offset=offset,
                )
            except Exception:  # noqa: BLE001
                break
            all_points.extend(batch)
            if offset is None or len(all_points) >= max_nodes:
                break

        all_points = all_points[:max_nodes]
        result: list[dict[str, Any]] = []
        for p in all_points:
            vec = p.vector.get("dense") if isinstance(p.vector, dict) else None
            if vec is None:
                continue
            result.append({"vector": vec, "payload": {"id": str(p.id), **(p.payload or {})}})

        return result if result else None
