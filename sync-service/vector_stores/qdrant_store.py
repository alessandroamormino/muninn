"""QdrantVectorStore — full BaseVectorStore implementation for Qdrant.

Engine: VECTOR_STORE_ENGINE=qdrant
URL format: http://localhost:6333

Search modes supported (QDRANT_MODES = hybrid | vector | bm25 | fts):
  - hybrid: dense KNN + sparse BM25 via RRF fusion (Prefetch + FusionQuery)
  - vector: dense KNN only (Ollama embeddings required)
  - bm25/fts: scroll with per-term MatchText filter (Snowball + Levenshtein fuzzy expansion).
              Each query term + Levenshtein-1 vocab variants → separate MatchText conditions
              in `should` (OR). Each MatchText applies Snowball independently — MatchTextAny
              does NOT preserve per-variant Snowball stemming. Results scored by field priority
              (text_fields config order): primary field match → higher score.

Qdrant collection schema per search_mode:
  - hybrid/vector: vectors_config={"dense": VectorParams(size=dims, distance=COSINE)}
  - hybrid/bm25/fts: sparse_vectors_config={"sparse": SparseVectorParams(modifier=IDF)}
  - hybrid/bm25/fts: create_payload_index("_fts_text", TextIndexParams(stemmer=Snowball))

_fts_text payload: always stored; contains joined text_fields values.
_fts_{field} payload: stored per text_field; used for field-weighted scoring at search time.

UUID format: str(uuid5(NAMESPACE_DNS, source_type + ":" + record_id))
(Pitfall 5: Qdrant requires standard UUID string format for PointStruct.id)

Filter note: Qdrant does NOT lowercase first char of field names (unlike Weaviate).
Use campo as-is from parsed_filter_pairs.
"""
from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from qdrant_client import QdrantClient
from qdrant_client import models as qmodels

from vector_stores.base import BaseVectorStore, IndexResult, SearchHit, compute_record_uuid
from vector_stores.synonyms import _get_omw_synonyms
from vector_stores.fuzzy import _apply_fuzzy_expansion

logger = logging.getLogger(__name__)

_EMBED_BATCH_SIZE = 10_000     # records per index_records call; OpenAI adapter sub-batches internally
_QDRANT_UPSERT_BATCH = 3_000  # points per gRPC upsert; protobuf ~18MB at 1536 dims (3× smaller than JSON)
_FTS_UPSERT_BATCH_SIZE = 100  # fts/bm25: no embedding, larger batches = fewer round-trips

# Phase 23: fuzzy expansion vocabulary, keyed by collection name.
# Populated by index_records() in fts/bm25 modes via _fts_text scroll.
# Capped at 50K tokens per collection. Consumed by api/search.py via
# `from vector_stores.qdrant_store import _fuzzy_vocab`.
_fuzzy_vocab: dict[str, frozenset[str]] = {}
_FUZZY_VOCAB_CAP = 50_000
_FUZZY_VOCAB_SCROLL_LIMIT = 10_000
_SYNONYMS_PAYLOAD_CAP = 50

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
    """Extract FTS stemmer language from cfg.vector_store.fts.language (default: 'en')."""
    try:
        return cfg.vector_store.fts.language or "en"
    except AttributeError:
        return "en"


def _snowball_language(lang: str) -> Any:
    """Map lang code to qdrant_client.models.SnowballLanguage enum value."""
    attr = _SNOWBALL_MAP.get(lang.lower(), "ENGLISH")
    return getattr(qmodels.SnowballLanguage, attr)


def _build_fts_per_term_conditions(query: str, vocab: frozenset, lang: str) -> list:
    """Per-term MatchText with fuzzy variants.

    Each term + Levenshtein-1 vocab variants → should=[MatchText(v) for v in variants].
    Each MatchText applies Snowball independently — MatchTextAny does NOT preserve
    per-variant Snowball stemming, causing morphological matches to be missed.
    When vocab is empty or python-Levenshtein absent: single MatchText per term (graceful).
    """
    terms = query.split() if query.strip() else []
    conditions: list = []
    for term in terms:
        expanded = _apply_fuzzy_expansion(term, vocab, lang=lang)
        variants = list(dict.fromkeys(expanded.split()))
        if len(variants) == 1:
            conditions.append(
                qmodels.FieldCondition(key="_fts_text", match=qmodels.MatchText(text=term))
            )
        else:
            conditions.append(qmodels.Filter(should=[
                qmodels.FieldCondition(key="_fts_text", match=qmodels.MatchText(text=v))
                for v in variants
            ]))
    return conditions


def _score_by_field(
    points: list,
    text_fields: list[str],
    query_variants: list[str],
) -> list[SearchHit]:
    """Score FTS/BM25 results by field priority (config order = weight order).

    Score = sum(1.0/(1+i) for each field i whose _fts_{field} payload contains a query variant).
    Falls back to score=1.0 when per-field payloads absent (old collections / Snowball-only match).
    Results sorted highest score first.
    """
    if not text_fields or not query_variants:
        return [SearchHit(properties=p.payload or {}, score=1.0) for p in points]

    lower_variants = {v.lower() for v in query_variants}

    def _field_score(payload: dict) -> float:
        score = 0.0
        for i, field in enumerate(text_fields):
            field_text = str(payload.get(f"_fts_{field}", "") or payload.get(field, "") or "")
            if not field_text:
                continue
            tokens = set(re.findall(r"[\w]+", field_text.lower()))
            if lower_variants & tokens:
                score += 1.0 / (1 + i)
        return score if score > 0 else 1.0

    hits = [
        SearchHit(properties=p.payload or {}, score=_field_score(p.payload or {}))
        for p in points
    ]
    hits.sort(key=lambda h: -h.score)
    return hits


class QdrantVectorStore(BaseVectorStore):
    """Qdrant implementation of BaseVectorStore.

    Constructor takes the Qdrant URL (e.g. "http://localhost:6333").
    Call open() before any operation; close() when done.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Any = None  # QdrantClient | None
        # Tracks active HNSW staging per collection (begin_bulk_load / end_bulk_load).
        self._staging_active: dict[str, bool] = {}
        # Thread-local storage for per-thread QdrantClient instances used in parallel upserts.
        # Each thread gets its own client so model_embedder._embed_storage is never shared.
        self._thread_local = threading.local()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open connection to Qdrant."""
        self._client = QdrantClient(url=self._url, prefer_grpc=True)
        logger.info("QdrantVectorStore: connected to %s (gRPC preferred)", self._url)

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

        collection = cfg.vector_store.collection
        mode = getattr(cfg.vector_store, "search_mode", "hybrid")

        if self._client.collection_exists(collection):
            return False

        # Phase 24: read qdrant_opts for on_disk + SQ quantization config
        qdrant_opts = getattr(cfg.vector_store, "qdrant_opts", None)
        _on_disk = bool(getattr(qdrant_opts, "on_disk", False)) if qdrant_opts else False
        _quant_cfg_obj = getattr(qdrant_opts, "quantization", None) if qdrant_opts else None
        _quant_type = getattr(_quant_cfg_obj, "type", "none") if _quant_cfg_obj else "none"

        # Build vectors_config (dense, for hybrid/vector modes)
        vectors_cfg: dict = {}
        if mode in ("hybrid", "vector"):
            # dims from config _embedding_dims if available, else default to 2560 (qwen3-embedding:4b)
            dims = getattr(cfg.vector_store, "_embedding_dims", None) or 2560
            # Build VectorParams kwargs — on_disk and SQ are conditional (Phase 24)
            _vp_kwargs: dict = {"size": dims, "distance": qmodels.Distance.COSINE}
            if _on_disk:
                _vp_kwargs["on_disk"] = True
                # Keep HNSW graph index in RAM even when raw vectors are on disk
                # (HnswConfigDiff(on_disk=False) is the default but must be explicit here)
                _vp_kwargs["hnsw_config"] = qmodels.HnswConfigDiff(on_disk=False)
            if _quant_type == "sq":
                _quantile = getattr(_quant_cfg_obj, "quantile", 0.99)
                _always_ram = getattr(_quant_cfg_obj, "always_ram", True)
                _vp_kwargs["quantization_config"] = qmodels.ScalarQuantization(
                    scalar=qmodels.ScalarQuantizationConfig(
                        type=qmodels.ScalarType.INT8,
                        quantile=_quantile,
                        always_ram=_always_ram,
                    )
                )
            elif _quant_type == "bq":
                _always_ram = getattr(_quant_cfg_obj, "always_ram", True)
                _vp_kwargs["quantization_config"] = qmodels.BinaryQuantization(
                    binary=qmodels.BinaryQuantizationConfig(
                        always_ram=_always_ram,
                    )
                )
            vectors_cfg["dense"] = qmodels.VectorParams(**_vp_kwargs)

        # Build sparse_vectors_config (BM25 sparse, for hybrid/bm25/fts modes).
        # Multi-sparse: >1 text_field → named sparse vectors per field (sparse_{field}).
        # Single-field or hybrid: legacy "sparse" name for backward compat.
        # NOTE: hybrid keeps single "sparse" slot — weighted multi-field RRF for
        # hybrid is out of scope for Phase 23 (fts/bm25 modes only).
        sparse_cfg: dict | None = None
        if mode in ("hybrid", "bm25", "fts"):
            text_fields_dict: dict[str, float] = cfg.vector_store.text_fields or {}
            field_names = list(text_fields_dict.keys())
            if mode in ("bm25", "fts") and len(field_names) > 1:
                sparse_cfg = {
                    f"sparse_{field}": qmodels.SparseVectorParams(modifier=qmodels.Modifier.IDF)
                    for field in field_names
                }
            else:
                sparse_cfg = {
                    "sparse": qmodels.SparseVectorParams(modifier=qmodels.Modifier.IDF)
                }

        self._client.create_collection(
            collection_name=collection,
            vectors_config=vectors_cfg,
            sparse_vectors_config=sparse_cfg,
        )
        logger.info(
            "QdrantVectorStore: created collection %r (mode=%r, dims=%s, fields=%s)",
            collection, mode, dims if mode in ("hybrid", "vector") else "n/a",
            list((cfg.vector_store.text_fields or {}).keys()),
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

        # Persist current quantization key for Phase 24 change detection on next startup
        try:
            from vector_stores.quantization_state import write_stored_quantization_key, _quant_key
            write_stored_quantization_key(collection, _quant_key(cfg))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not write quantization_state for %r: %s", collection, exc)

        return True

    def drop_index(self, collection_name: str) -> None:
        """Drop the Qdrant collection if it exists."""
        if self._client.collection_exists(collection_name):
            logger.info("QdrantVectorStore: dropping collection %r", collection_name)
            self._client.delete_collection(collection_name)
        else:
            logger.info("QdrantVectorStore: collection %r does not exist; nothing to drop.", collection_name)

    # ------------------------------------------------------------------
    # Snapshot / restore (Phase 26 — entity load/unload management)
    # ------------------------------------------------------------------

    def supports_snapshots(self) -> bool:
        """Qdrant supports native snapshot/restore (D-06)."""
        return True

    def snapshot_collection(self, collection_name: str) -> str:
        """Create a Qdrant snapshot for collection_name. Returns the snapshot file name.

        NEVER constructs the snapshot name itself — always reads it from the
        SnapshotDescription returned by create_snapshot (Pitfall 1 / Assumption A1
        in 26-RESEARCH.md): the exact naming pattern is not a stable API contract.
        """
        desc = self._client.create_snapshot(collection_name=collection_name, wait=True)
        if desc is None:
            raise RuntimeError(f"create_snapshot returned None for {collection_name!r}")
        logger.info(
            "QdrantVectorStore: snapshot created %r for collection %r (size=%s bytes)",
            desc.name, collection_name, desc.size,
        )
        return desc.name

    def restore_collection(self, collection_name: str, snapshot_name: str) -> None:
        """Restore collection_name from a previously created LOCAL snapshot.

        IMPORTANT: does NOT call create_index() first — recover_snapshot creates the
        collection if absent, or overwrites it if present (Qdrant native behavior).
        The location uses the file:// URI scheme interpreted INSIDE the Qdrant
        container filesystem (NOT the orchestrator's — Pitfall 1 in 26-RESEARCH.md).
        recover_snapshot returns False/None (not an exception) on failure — the
        return value must be checked explicitly (Pitfall 4).
        """
        location = f"file:///qdrant/snapshots/{collection_name}/{snapshot_name}"
        ok = self._client.recover_snapshot(
            collection_name=collection_name,
            location=location,
            wait=True,
        )
        if not ok:
            raise RuntimeError(
                f"recover_snapshot failed for {collection_name!r} from {location!r}"
            )
        logger.info(
            "QdrantVectorStore: collection %r restored from snapshot %r",
            collection_name, snapshot_name,
        )

    def list_collection_snapshots(self, collection_name: str) -> list[str]:
        """Return snapshot file names for a collection, newest first."""
        snaps = self._client.list_snapshots(collection_name=collection_name)
        snaps_sorted = sorted(snaps, key=lambda s: s.creation_time or "", reverse=True)
        return [s.name for s in snaps_sorted]

    def delete_collection_snapshot(self, collection_name: str, snapshot_name: str) -> None:
        """Delete a specific snapshot file (cleanup of stale duplicates)."""
        self._client.delete_snapshot(
            collection_name=collection_name, snapshot_name=snapshot_name, wait=True,
        )

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    def begin_bulk_load(self, collection_name: str, mode: str) -> None:
        """Disable HNSW index building before streaming bulk upsert (m=0 staging).

        Called by SyncEngine.run_full() before the streaming loop. Works together
        with end_bulk_load() to bracket the entire multi-chunk ingestion so the
        HNSW graph is built once at the end rather than incrementally per-chunk.
        Only applies to hybrid/vector modes that have a dense HNSW index.
        """
        if mode not in ("hybrid", "vector"):
            return
        try:
            self._client.update_collection(
                collection_name=collection_name,
                vectors_config={
                    "dense": qmodels.VectorParamsDiff(
                        hnsw_config=qmodels.HnswConfigDiff(m=0)
                    )
                },
            )
            self._staging_active[collection_name] = True
            logger.info(
                "QdrantVectorStore begin_bulk_load: HNSW disabled (m=0) for %r",
                collection_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "begin_bulk_load: m=0 failed for %r (non-fatal): %s", collection_name, exc
            )
            self._staging_active[collection_name] = False

    def end_bulk_load(self, collection_name: str) -> None:
        """Restore HNSW production settings after streaming bulk upsert.

        Called by SyncEngine.run_full() in a finally block so restore runs even
        if the streaming loop raises (prevents collection stuck at m=0 permanently).
        """
        if not self._staging_active.get(collection_name):
            self._staging_active.pop(collection_name, None)
            return
        try:
            self._client.update_collection(
                collection_name=collection_name,
                vectors_config={
                    "dense": qmodels.VectorParamsDiff(
                        hnsw_config=qmodels.HnswConfigDiff(m=16, ef_construct=200)
                    )
                },
            )
            logger.info(
                "QdrantVectorStore end_bulk_load: HNSW rebuilt (m=16, ef_construct=200) for %r",
                collection_name,
            )
            # Nudge the optimizer so it re-evaluates segment index state immediately.
            # Without this, Qdrant may stay yellow after m=0→m=16 restore until the
            # background optimizer timer fires (collection is fully indexed but stuck yellow).
            self._client.update_collection(
                collection_name=collection_name,
                optimizers_config=qmodels.OptimizersConfigDiff(indexing_threshold=10000),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "end_bulk_load: m=16 restore failed for %r: %s", collection_name, exc
            )
        finally:
            self._staging_active.pop(collection_name, None)

    def index_records(
        self,
        records: list[dict[str, Any]],
        cfg: Any,
        source_type: str,
        embedding_adapter: Any = None,
        id_field: str | None = None,
        start_from_batch: int = 0,
        batch_num_offset: int = 0,
        on_batch_done: Callable[[int, int, int], None] | None = None,
        is_full_index: bool = False,
    ) -> IndexResult:
        """Upsert records into Qdrant collection.

        - fts/bm25 mode: skips embedding entirely (both use sparse BM25 server-side)
        - other modes: computes dense embeddings in batches with dedup (mirrors weaviate upsert.py)
        - Always stores _fts_text payload field (joined text_fields)
        - UUID deterministic: str(uuid5(NAMESPACE_DNS, source_type:record_id))
        """
        mode = getattr(cfg.vector_store, "search_mode", "hybrid")
        if id_field is None:
            id_field = cfg.source.id_field
        collection = cfg.vector_store.collection
        text_fields_cfg: dict[str, float] = cfg.vector_store.text_fields or {}
        text_fields: list[str] = list(text_fields_cfg.keys())
        use_multi_sparse = mode in ("bm25", "fts") and len(text_fields) > 1

        # ------------------------------------------------------------------ #
        # FAST PATH — fts / bm25: no embedding, parallel upsert             #
        # ------------------------------------------------------------------ #
        if mode in ("fts", "bm25"):
            use_omw = bool(getattr(cfg.vector_store.fts, "use_omw", False))
            fts_lang = getattr(cfg.vector_store.fts, "language", "en")

            # Build all PointStructs in a single Python pass (CPU only, no IO)
            all_points: list = []
            for record in records:
                raw_id = record.get(id_field)
                if raw_id is None or raw_id == "":
                    continue
                obj_uuid = str(compute_record_uuid(source_type, str(raw_id)))
                payload = {k: v for k, v in record.items() if v is not None and v != ""}
                fts_text = " ".join(
                    str(record.get(f, "")) for f in text_fields if record.get(f)
                )
                payload["_fts_text"] = fts_text
                for field in text_fields:
                    payload[f"_fts_{field}"] = str(record.get(field, "") or "")

                # _synonyms payload (fts/bm25 modes only; empty list when use_omw=False)
                synonyms_list: list[str] = []
                if use_omw:
                    seen_syns: set[str] = set()
                    for field in text_fields:
                        text = str(record.get(field, "")).lower()
                        for token in re.findall(r"[\w]+", text):
                            if len(synonyms_list) >= _SYNONYMS_PAYLOAD_CAP:
                                break
                            for lemma in _get_omw_synonyms(token, fts_lang):
                                if lemma not in seen_syns:
                                    seen_syns.add(lemma)
                                    synonyms_list.append(lemma)
                                    if len(synonyms_list) >= _SYNONYMS_PAYLOAD_CAP:
                                        break
                        if len(synonyms_list) >= _SYNONYMS_PAYLOAD_CAP:
                            break
                payload["_synonyms"] = synonyms_list

                # Multi-sparse: per-field Documents; single-sparse: joined _fts_text
                if use_multi_sparse:
                    vector = {
                        f"sparse_{field}": qmodels.Document(
                            text=str(record.get(field, "")), model="Qdrant/bm25"
                        )
                        for field in text_fields if record.get(field) is not None
                    }
                else:
                    vector = {"sparse": qmodels.Document(text=fts_text, model="Qdrant/bm25")}

                all_points.append(
                    qmodels.PointStruct(id=obj_uuid, payload=payload, vector=vector)
                )

            total = len(all_points)
            batches = [
                all_points[i: i + _FTS_UPSERT_BATCH_SIZE]
                for i in range(0, total, _FTS_UPSERT_BATCH_SIZE)
            ]
            logger.info(
                "QdrantVectorStore FTS fast-path: %d points → %d batches × %d (sequential)",
                total, len(batches), _FTS_UPSERT_BATCH_SIZE,
            )

            inserted_count = 0

            for bi, batch in enumerate(batches):
                if bi < start_from_batch:
                    continue
                self._client.upsert(collection_name=collection, points=batch, wait=True)
                inserted_count += len(batch)
                if on_batch_done:
                    on_batch_done(bi, inserted_count, total)

            logger.info(
                "QdrantVectorStore.index_records: upserted %d points to %r (mode=%r)",
                inserted_count, collection, mode,
            )
            self._build_fuzzy_vocab(collection)
            return IndexResult(inserted=inserted_count, updated=0, skipped=0)

        # ------------------------------------------------------------------ #
        # SLOW PATH — hybrid / vector: interleaved embed + upsert per batch  #
        # ------------------------------------------------------------------ #
        # Each batch: embed → build points → upsert → checkpoint.
        # Keeps only _EMBED_BATCH_SIZE vectors in memory at any time (no
        # all_vecs accumulation). batch_num gates both embedding and upsert:
        # batches < start_from_batch are skipped entirely so vector-less
        # points are never produced on resume (replaces CR-01 two-phase approach).
        total_records = len(records)
        inserted = 0
        # batch_num_offset shifts the counter so on_batch_done reports global
        # batch numbers when index_records() is called per-chunk from run_full().
        batch_num = batch_num_offset - 1

        # Phase 24-02: HNSW staging bulk-load pattern.
        # Temporarily disable HNSW index building (m=0) before a full-index bulk upsert,
        # then rebuild with production settings (m=16, ef_construct=200) after all batches
        # complete. This prevents Qdrant optimizer thrashing during initial 1M+ record ingestion
        # and provides 5-10x faster ingest for large datasets.
        # Gate: only for full re-index (is_full_index=True), hybrid/vector modes (have dense
        # HNSW), and non-empty record sets (nothing to upsert = no staging needed).
        _staging = (
            is_full_index
            and mode in ("hybrid", "vector")
            and total_records > 0
        )
        if _staging:
            try:
                self._client.update_collection(
                    collection_name=collection,
                    vectors_config={
                        "dense": qmodels.VectorParamsDiff(
                            hnsw_config=qmodels.HnswConfigDiff(m=0)
                        )
                    },
                )
                logger.info(
                    "QdrantVectorStore staging: HNSW disabled (m=0) for bulk upsert on %r",
                    collection,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Staging m=0 failed for %r (non-fatal): %s", collection, exc
                )
                _staging = False

        try:
            for batch_start in range(0, total_records, _EMBED_BATCH_SIZE):
                batch_records = records[batch_start: batch_start + _EMBED_BATCH_SIZE]
                batch_num += 1

                if batch_num < start_from_batch:
                    continue

                # Embed this batch (with within-batch dedup)
                if embedding_adapter is not None:
                    batch_docs = [
                        " ".join(str(r.get(f, "")) for f in text_fields if r.get(f))
                        for r in batch_records
                    ]
                    unique_docs = list(dict.fromkeys(batch_docs))
                    if len(unique_docs) < len(batch_docs):
                        logger.debug(
                            "Embedding dedup batch %d: %d → %d unique",
                            batch_num, len(batch_docs), len(unique_docs),
                        )
                    batch_vecs_list = embedding_adapter.embed(unique_docs)
                    vec_map: dict[str, list[float]] = dict(zip(unique_docs, batch_vecs_list))
                    batch_vecs: list[list[float] | None] = [vec_map.get(d) for d in batch_docs]
                else:
                    batch_vecs = [None] * len(batch_records)

                # Pre-compute BM25 sparse vectors in one batch call before building PointStructs.
                # This avoids the qdrant-client model_embedder thread-safety issue and allows
                # parallel upsert threads to do pure I/O (GIL-releasing) instead of CPU-bound BM25.
                fts_texts = [
                    " ".join(str(r.get(f, "")) for f in text_fields if r.get(f))
                    for r in batch_records
                ]
                sparse_vecs: list[Any] = []
                if mode == "hybrid":
                    sparse_vecs = list(self._client._sparse_embed_documents(
                        fts_texts, embedding_model_name="Qdrant/bm25"
                    ))

                # Build PointStructs for this batch
                points_batch: list = []
                for idx, (record, vec) in enumerate(zip(batch_records, batch_vecs)):
                    raw_id = record.get(id_field)
                    if raw_id is None or raw_id == "":
                        continue
                    obj_uuid = str(compute_record_uuid(source_type, str(raw_id)))
                    payload = {k: v for k, v in record.items() if v is not None and v != ""}
                    fts_text = fts_texts[idx]
                    payload["_fts_text"] = fts_text
                    for field in text_fields:
                        payload[f"_fts_{field}"] = str(record.get(field, "") or "")

                    vector: dict = {}
                    if mode in ("hybrid", "vector") and vec is not None:
                        vector["dense"] = vec
                    if mode == "hybrid" and sparse_vecs:
                        vector["sparse"] = sparse_vecs[idx]

                    points_batch.append(
                        qmodels.PointStruct(id=obj_uuid, payload=payload, vector=vector)
                    )

                if not points_batch:
                    continue

                sub_batches = [
                    points_batch[i: i + _QDRANT_UPSERT_BATCH]
                    for i in range(0, len(points_batch), _QDRANT_UPSERT_BATCH)
                ]
                def _do_upsert(batch: list, url: str = self._url, coll: str = collection) -> None:
                    # Each thread gets its own QdrantClient so model_embedder._embed_storage
                    # is never shared across threads (qdrant-client is not thread-safe).
                    if not hasattr(self._thread_local, "client"):
                        self._thread_local.client = QdrantClient(url=url, prefer_grpc=True)
                    self._thread_local.client.upsert(
                        collection_name=coll, points=batch, wait=True,
                    )
                with ThreadPoolExecutor(max_workers=len(sub_batches)) as ex:
                    futures = [ex.submit(_do_upsert, b) for b in sub_batches]
                    for f in as_completed(futures):
                        f.result()  # re-raise any exception from the worker
                inserted += len(points_batch)
                if on_batch_done:
                    on_batch_done(batch_num, inserted, total_records)

        finally:
            # Phase 24-02: restore HNSW index (m=16, ef_construct=200) after all upserts.
            # finally block ensures restore runs even if an upsert raises, preventing the
            # collection from being permanently left at m=0 (WR-01 fix).
            if _staging:
                try:
                    self._client.update_collection(
                        collection_name=collection,
                        vectors_config={
                            "dense": qmodels.VectorParamsDiff(
                                hnsw_config=qmodels.HnswConfigDiff(m=16, ef_construct=200)
                            )
                        },
                    )
                    logger.info(
                        "QdrantVectorStore staging: HNSW rebuilt (m=16, ef_construct=200) for %r",
                        collection,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Staging m=16 restore failed for %r: %s", collection, exc
                    )

        logger.info(
            "QdrantVectorStore.index_records: upserted %d points to %r (mode=%r)",
            inserted, collection, mode,
        )
        # Hybrid/vector mode: no fuzzy vocab (fts/bm25 specific)
        return IndexResult(inserted=inserted, updated=0, skipped=0)

    def search(
        self,
        query: str,
        query_vector: list[float] | None,
        cfg: Any,
        filters: list[tuple[str, str]] | None = None,
        limit: int = 10,
        mode: str = "hybrid",
        must_not_text_terms: list[str] | None = None,
        match_mode_override: str | None = None,
    ) -> list[SearchHit]:
        """Execute search using the appropriate Qdrant query API for search_mode.

        - hybrid: RRF fusion of sparse BM25 + dense KNN (Prefetch + FusionQuery)
        - vector: dense KNN only
        - bm25:   sparse BM25 only via models.Document
        - fts:    SAME as bm25 — sparse BM25 query (NOT MatchText filter — PITFALL 1)

        Filters: FieldCondition with MatchValue. Qdrant does NOT lowercase field names
        (unlike Weaviate). Use campo as-is.

        must_not_text_terms: when provided for fts/bm25 mode, uses scroll+must_not instead
        of BM25 query. BM25 cannot return records that don't contain the query terms, so
        negation queries like "chi NON lavora su X" would return empty results — scroll
        fetches all records and excludes those matching the negated entity server-side.
        """
        collection = cfg.vector_store.collection

        # Build must conditions from equality filters
        must_conditions = []
        if filters:
            must_conditions = [
                qmodels.FieldCondition(key=campo, match=qmodels.MatchValue(value=valore))
                for campo, valore in filters
            ]

        # Build must_not conditions from negated text terms
        must_not_conditions = []
        if must_not_text_terms:
            must_not_conditions = [
                qmodels.FieldCondition(key="_fts_text", match=qmodels.MatchText(text=term))
                for term in must_not_text_terms
            ]

        # --- fts/bm25: per-term MatchText filter + Snowball + fuzzy + field scoring ----
        if mode in ("bm25", "fts"):
            _resolved_match_mode = match_mode_override or getattr(
                cfg.vector_store.fts, "match_mode", "and"
            )
            lang = getattr(cfg.vector_store.fts, "language", "en")
            vocab = _fuzzy_vocab.get(collection, frozenset())
            text_fields_order = list((cfg.vector_store.text_fields or {}).keys())

            per_term_conds = _build_fts_per_term_conditions(query, vocab, lang)

            if _resolved_match_mode == "and":
                all_must = (must_conditions or []) + per_term_conds
                qdrant_filter = qmodels.Filter(must=all_must) if all_must else None
            else:
                if must_conditions and per_term_conds:
                    qdrant_filter = qmodels.Filter(must=must_conditions, should=per_term_conds)
                elif per_term_conds:
                    qdrant_filter = qmodels.Filter(should=per_term_conds)
                elif must_conditions:
                    qdrant_filter = qmodels.Filter(must=must_conditions)
                else:
                    qdrant_filter = None

            if must_not_conditions:
                if qdrant_filter is not None:
                    qdrant_filter = qmodels.Filter(
                        must=list(qdrant_filter.must or []) or None,
                        should=list(qdrant_filter.should or []) or None,
                        must_not=must_not_conditions,
                    )
                else:
                    qdrant_filter = qmodels.Filter(must_not=must_not_conditions)

            fts_points, _ = self._client.scroll(
                collection_name=collection,
                scroll_filter=qdrant_filter,
                limit=limit,
                with_payload=True,
            )
            all_variants = list(dict.fromkeys(
                v
                for term in (query.split() if query.strip() else [])
                for v in _apply_fuzzy_expansion(term, vocab, lang=lang).split()
            ))
            return _score_by_field(fts_points, text_fields_order, all_variants)

        # NOTE: Qdrant does NOT lowercase first char (unlike Weaviate) — campo used as-is
        qdrant_filter = None
        if must_conditions:
            qdrant_filter = qmodels.Filter(must=must_conditions)

        # Phase 24: build rescoring SearchParams when SQ/BQ quantization is active + rescore=True
        _qdrant_opts = getattr(cfg.vector_store, "qdrant_opts", None)
        _quant_type_s = (
            getattr(getattr(_qdrant_opts, "quantization", None), "type", "none")
            if _qdrant_opts else "none"
        )
        _rescore = (
            bool(getattr(getattr(_qdrant_opts, "search", None), "rescore", False))
            if _qdrant_opts else False
        )
        _oversampling = (
            float(getattr(getattr(_qdrant_opts, "search", None), "oversampling", 2.0))
            if _qdrant_opts else 2.0
        )
        search_params = None
        if _quant_type_s != "none" and _rescore:
            search_params = qmodels.SearchParams(
                quantization=qmodels.QuantizationSearchParams(
                    ignore=False,
                    rescore=True,
                    oversampling=_oversampling,
                )
            )

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
                        params=search_params,  # rescore must be on the leaf dense prefetch
                    ),
                ],
                query=qmodels.FusionQuery(fusion=qmodels.Fusion.RRF),
                limit=limit,
                with_payload=True,
                query_filter=qdrant_filter,
                search_params=search_params,  # kept for API observability; Qdrant ignores at fusion level
            )
        elif mode == "vector":
            results = self._client.query_points(
                collection_name=collection,
                query=query_vector,
                using="dense",
                limit=limit,
                with_payload=True,
                query_filter=qdrant_filter,
                search_params=search_params,
            )
        else:
            raise ValueError(f"Unknown search mode: {mode!r}. Supported: hybrid, vector, bm25, fts")

        return [SearchHit(properties=p.payload, score=p.score) for p in results.points]

    def _build_fuzzy_vocab(self, collection: str) -> None:
        """Build in-memory term vocabulary from _fts_text scroll for fuzzy expansion.

        Paginates through the entire collection (following next_page_offset) so every
        record contributes to the vocab, regardless of collection size. Stops early when
        _FUZZY_VOCAB_CAP unique tokens are collected. Stored in module-level _fuzzy_vocab.
        Called after index_records in fts/bm25 modes. Gracefully no-ops on any error.
        """
        try:
            tokens: set[str] = set()
            offset = None
            while True:
                points, next_offset = self._client.scroll(
                    collection_name=collection,
                    scroll_filter=None,
                    limit=_FUZZY_VOCAB_SCROLL_LIMIT,
                    offset=offset,
                    with_payload=["_fts_text"],
                )
                for p in points:
                    text = (p.payload or {}).get("_fts_text", "")
                    for token in re.findall(r"[\w]+", text.lower()):
                        tokens.add(token)
                        if len(tokens) >= _FUZZY_VOCAB_CAP:
                            break
                    if len(tokens) >= _FUZZY_VOCAB_CAP:
                        break
                if len(tokens) >= _FUZZY_VOCAB_CAP or next_offset is None or not points:
                    break
                offset = next_offset
            _fuzzy_vocab[collection] = frozenset(tokens)
            logger.info(
                "fuzzy vocab built: %d tokens for %r", len(_fuzzy_vocab[collection]), collection
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("fuzzy vocab build failed for %r: %s", collection, exc)

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
