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
        """Drop + recreate collection + upsert all records from scratch.

        Resumable: se esiste un checkpoint per questa collection, riprende dal batch
        successivo all'ultimo completato senza riscaricare né ri-droppare.
        """
        collection_name = self._cfg.vector_store.collection

        # --- Checkpoint: resume o fresh start? -----------------------------------
        ckpt = checkpoint.read(collection_name)
        collection_exists = self._vector_store.index_exists(collection_name)
        resuming = ckpt is not None and collection_exists

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
                "Avvio full re-index (source.type=%r, collection=%r)",
                self._cfg.source.type, collection_name,
            )
            start_from_batch = 0
            if collection_exists:
                logger.info("Cancellazione collezione %r...", collection_name)
                self._vector_store.drop_index(collection_name)
            self._vector_store.create_index(self._cfg)
            self._state.clear()
            checkpoint.write(collection_name, last_completed_batch=-1)
        # -------------------------------------------------------------------------

        if on_progress:
            on_progress("fetching", 0, 0)
        records = self._source_adapter.fetch_records()
        logger.info("Sorgente ha restituito %d record per full re-index", len(records))

        def _on_batch_done(batch_num: int, done: int, total: int) -> None:
            checkpoint.write(collection_name, last_completed_batch=batch_num)
            if on_progress:
                on_progress("embedding", done, total)

        result = self._vector_store.index_records(
            records, self._cfg, self._cfg.source.type, self._embedding_adapter,
            id_field=self._id_field,
            start_from_batch=start_from_batch,
            on_batch_done=_on_batch_done,
            is_full_index=True,
        )

        # Persist state for all records (both resumed and new batches)
        self._persist_state(records, result)

        # Elimina checkpoint — sync completato con successo
        checkpoint.delete(collection_name)

        # Aggiorna model_version.json per evitare re-index spurio al prossimo avvio.
        try:
            self._write_model_version_fn(self._cfg.embedding.model)
        except OSError as exc:
            logger.warning("Could not update model_version.json after full re-index: %s", exc)

        stats = self._build_stats(result, skipped=0)
        if resuming:
            stats["resumed_from_batch"] = start_from_batch
        return stats

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _persist_state(self, records: list[dict], result: Any) -> None:
        """Aggiorna StateStore per tutti i record upsertati con successo.

        Usa bulk_set per costruire il dict in memoria e scrivere su disco
        una sola volta — evita O(n²) scritture su dataset grandi.
        """
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
