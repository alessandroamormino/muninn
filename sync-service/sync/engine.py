"""SyncEngine — orchestrazione del pipeline fetch -> hash -> diff -> upsert.

Implementa:
  - run_incremental(): upserta solo i record con hash modificato
  - run_full(): drop + recreate collezione + upsert completo

Dipende da:
  - StateStore (sync/state_store.py)
  - BaseVectorStore (vector_stores/base.py) — engine-agnostic interface
  - build_source_adapter (sources/__init__.py)
  - write_stored_model (weaviate_store/model_version.py) — still model_version.json
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from config.settings import AppConfig
from embeddings import build_embedding_adapter
from sources import build_source_adapter
from sync import checkpoint
from sync.state_store import StateStore
from vector_stores.base import BaseVectorStore, IndexResult, compute_record_uuid
from weaviate_store.model_version import write_stored_model

logger = logging.getLogger(__name__)

# Batch size constant (mirrors weaviate_store/upsert.py _EMBED_BATCH_SIZE)
_EMBED_BATCH_SIZE = 1000


def _default_write_model_version(model: str) -> None:
    write_stored_model(model)


class SyncEngine:
    """Orchestrates the fetch -> hash -> diff -> upsert pipeline."""

    def __init__(
        self,
        app_cfg: AppConfig,
        vector_store: BaseVectorStore,
        state_store: StateStore,
        cache_store: Any | None = None,
    ) -> None:
        self._cfg = app_cfg
        self._vector_store = vector_store
        self._state = state_store
        self._cache_store = cache_store  # reserved for future direct invalidation
        self._source_adapter = build_source_adapter(
            app_cfg.source, app_cfg.sync, app_cfg.vector_store
        )
        self._embedding_adapter = build_embedding_adapter(app_cfg.embedding)
        # Iniettabile nei test tramite override: engine._write_model_version_fn = mock_fn
        self._write_model_version_fn = _default_write_model_version
        # Effective id_field: for MySQL the authoritative value lives in source.mysql.query.id_field;
        # for all other adapters it lives at source.id_field (top-level SourceConfig).
        if (
            app_cfg.source.type == "mysql"
            and app_cfg.source.mysql is not None
        ):
            self._id_field: str = app_cfg.source.mysql.query.id_field
        else:
            self._id_field = app_cfg.source.id_field

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_incremental(self, on_progress: Callable[[str, int, int], None] | None = None) -> dict:
        """Fetch all records, compare hashes, upsert only changed/new records."""
        logger.info("Avvio sync incrementale (source.type=%r)", self._cfg.source.type)

        if on_progress:
            on_progress("fetching", 0, 0)
        records = self._source_adapter.fetch_records()
        logger.info("Sorgente ha restituito %d record", len(records))

        delta: list[dict] = []
        skipped = 0

        for record in records:
            record_id = self._source_adapter.get_record_id(record)
            current_hash = self._source_adapter.get_record_hash(record)
            stored = self._state.get(record_id)
            if stored is not None and stored.get("hash") == current_hash:
                skipped += 1
                continue
            delta.append(record)

        logger.info("Delta: %d record da upsertare, %d invariati", len(delta), skipped)

        result = self._vector_store.index_records(
            delta, self._cfg, self._cfg.source.type, self._embedding_adapter,
            id_field=self._id_field,
            on_batch_done=lambda bn, done, total: on_progress("embedding", done, total) if on_progress else None,
        )
        if result.skipped == 0:
            self._persist_state(delta, result)
        else:
            logger.warning(
                "%d record non upsertati con successo; hash non salvati per permettere "
                "il retry alla prossima run incrementale.",
                result.skipped,
            )

        return self._build_stats(result, skipped=skipped)

    def run_full(self, on_progress: Callable[[str, int, int], None] | None = None) -> dict:
        """Drop + recreate collection + stream all records through fetch→embed→upsert.

        Streaming pipeline: iterates source chunks one at a time via
        fetch_records_chunked(). RAM stays at O(chunk_size) regardless of dataset
        size — no more loading 1.5M records into a list before embedding starts.

        Resumable: checkpoint stores last completed batch; resume skips already-
        processed chunks (fast with keyset pagination — no OFFSET penalty).

        HNSW staging: begin_bulk_load() disables HNSW graph building before the
        loop; end_bulk_load() in finally restores m=16 after all chunks complete.
        index_records() is called per-chunk with is_full_index=False so it does
        not attempt its own staging.

        State: accumulated in-memory across chunks, flushed to disk once at end
        (avoids writing a growing JSON file 1500× for 1.5M records).
        """
        collection_name = self._cfg.vector_store.collection
        mode = getattr(self._cfg.vector_store, "search_mode", "hybrid")

        # --- Checkpoint: resume o fresh start? -----------------------------------
        ckpt = checkpoint.read(collection_name)
        collection_exists = self._vector_store.index_exists(collection_name)
        # Only resume when at least one batch completed (last_completed_batch >= 0).
        # A checkpoint at -1 means the collection was recreated but the first upsert
        # never finished — treat it as a fresh start so drop+create runs again.
        resuming = (
            ckpt is not None
            and collection_exists
            and ckpt.get("last_completed_batch", -1) >= 0
        )

        if resuming:
            start_from_batch = ckpt["last_completed_batch"] + 1
            logger.info(
                "RESUME full re-index %r dal batch %d (già processati: ~%d record).",
                collection_name, start_from_batch, start_from_batch * _EMBED_BATCH_SIZE,
            )
        else:
            if ckpt is not None and not collection_exists:
                logger.warning(
                    "Checkpoint trovato ma collection %r mancante — ripartendo da zero.",
                    collection_name,
                )
            logger.info(
                "Avvio full re-index streaming (source.type=%r, collection=%r)",
                self._cfg.source.type, collection_name,
            )
            start_from_batch = 0
            if collection_exists:
                logger.info("Cancellazione collezione %r...", collection_name)
                self._vector_store.drop_index(collection_name)
            # Inject actual embedding dims so Qdrant creates the collection with the
            # correct vector size. Only when a client-side adapter exists (Ollama/TEI);
            # weaviate_builtin returns None and handles dims server-side.
            if self._embedding_adapter is not None:
                object.__setattr__(
                    self._cfg.vector_store,
                    "_embedding_dims",
                    self._embedding_adapter.dimensions(),
                )
            self._vector_store.create_index(self._cfg)
            self._state.clear()
            checkpoint.write(collection_name, last_completed_batch=-1)
        # -------------------------------------------------------------------------

        if on_progress:
            on_progress("fetching", 0, 0)

        # HNSW staging around the entire streaming loop (restored in finally).
        self._vector_store.begin_bulk_load(collection_name, mode)

        total_inserted = 0
        total_fetched = 0
        total_upsert_errors = 0
        all_state_entries: dict[str, dict] = {}
        global_batch_num = -1

        try:
            for chunk in self._source_adapter.fetch_records_chunked(chunk_size=_EMBED_BATCH_SIZE):
                global_batch_num += 1
                total_fetched += len(chunk)

                if global_batch_num < start_from_batch:
                    # Chunk fetched (keyset = no OFFSET penalty) but discarded for resume.
                    continue

                if on_progress:
                    on_progress("embedding", total_inserted, total_fetched)

                # Capture loop-local value for closure (total_inserted at batch start).
                _inserted_before_batch = total_inserted

                def _on_batch_done(batch_num: int, done: int, _total: int,
                                   _ins=_inserted_before_batch) -> None:
                    checkpoint.write(collection_name, last_completed_batch=batch_num)
                    if on_progress:
                        on_progress("embedding", _ins + done, total_fetched)

                result = self._vector_store.index_records(
                    chunk,
                    self._cfg,
                    self._cfg.source.type,
                    self._embedding_adapter,
                    id_field=self._id_field,
                    start_from_batch=0,
                    batch_num_offset=global_batch_num,
                    on_batch_done=_on_batch_done,
                    is_full_index=False,
                )

                total_inserted += result.inserted
                total_upsert_errors += result.skipped
                all_state_entries.update(self._compute_state_entries(chunk))

        finally:
            self._vector_store.end_bulk_load(collection_name)

        # Single bulk_set write for all state (avoids N disk writes during streaming).
        if all_state_entries:
            logger.info("Persisting state per %d record (single write)...", len(all_state_entries))
            self._state.bulk_set(all_state_entries)
            logger.info("State persistito.")

        checkpoint.delete(collection_name)

        try:
            self._write_model_version_fn(self._cfg.embedding.model)
        except OSError as exc:
            logger.warning("Could not update model_version.json after full re-index: %s", exc)

        streaming_result = IndexResult(
            inserted=total_inserted,
            updated=0,
            skipped=total_upsert_errors,
        )
        stats = self._build_stats(streaming_result, skipped=0)
        if resuming:
            stats["resumed_from_batch"] = start_from_batch
        return stats

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_state_entries(self, records: list[dict]) -> dict[str, dict]:
        """Compute state dict for a list of records without writing to disk."""
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        entries: dict[str, dict] = {}
        for record in records:
            record_id = self._source_adapter.get_record_id(record)
            current_hash = self._source_adapter.get_record_hash(record)
            weaviate_uuid = str(compute_record_uuid(self._cfg.source.type, record_id))
            entries[record_id] = {
                "hash": current_hash,
                "synced_at": now_iso,
                "weaviate_uuid": weaviate_uuid,
            }
        return entries

    def _persist_state(self, records: list[dict], result: Any) -> None:
        """Aggiorna StateStore per tutti i record upsertati con successo (single write)."""
        entries = self._compute_state_entries(records)
        logger.info("Persisting state per %d record (single write)...", len(entries))
        self._state.bulk_set(entries)
        logger.info("State persistito.")

    def _build_stats(self, result: Any, skipped: int = 0) -> dict:
        total = result.total + skipped
        stats: dict = {
            "total": total,
            "inserted": result.inserted,
            "updated": result.updated,
            "skipped": skipped,
            "errors": result.skipped,  # UpsertResult.skipped = errori upsert
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        # D-21/D-22 (Phase 13.2): warn when large unquantized collection detected.
        # Key is added ONLY when condition is met -- absent key = no warning.
        q = getattr(self._cfg.vector_store, "quantization", "none")
        if total > 50_000 and q == "none":
            msg = (
                f"Collection has {total:,} records but quantization='none'. "
                "Consider enabling quantization (pq/bq/sq) to reduce RAM usage."
            )
            logger.warning("quantization_warning: %s", msg)
            stats["quantization_warning"] = msg
        return stats
