"""Weaviate upsert path — deterministic UUIDs + property-filtered writes.

When an embedding_adapter is provided (e.g. OllamaEmbeddingAdapter), vectors are
computed client-side and passed explicitly to Weaviate on every insert/replace.
When no adapter is provided, Weaviate handles vectorization server-side via its
built-in text2vec-transformers module.

Public functions:
  - compute_record_uuid(source_type, record_id) -> uuid.UUID
  - upsert_records(client, records, weaviate_cfg, source_type, embedding_adapter) -> UpsertResult
"""
from __future__ import annotations

import datetime as _dt
import logging
import uuid as _uuid
from dataclasses import dataclass
from typing import Any, Callable

from config.settings import WeaviateConfig

logger = logging.getLogger(__name__)

# Weaviate v4 reserves these names; inserting them as properties raises an error.
_WEAVIATE_RESERVED_PROPS = {"id", "vector"}
# Fields coerced to float (matches heuristic in schema.py _infer_metadata_datatype).
_NUMBER_FIELDS = {"price", "cost", "amount", "qty", "quantity", "score", "weight", "value", "popularity"}
_NUMBER_SUFFIXES = {"_average", "_count", "_rate", "_score", "_ratio", "_num", "_total", "_amount"}
_EMBED_BATCH_SIZE = 500  # max texts per Ollama call — bumped from 100 for throughput
_UPSERT_REPORT_EVERY = _EMBED_BATCH_SIZE  # report upsert progress at same cadence


@dataclass
class UpsertResult:
    inserted: int
    updated: int
    skipped: int

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.skipped


def compute_record_uuid(source_type: str, record_id: str) -> _uuid.UUID:
    """Deterministic UUID5 per D-11: uuid5(NAMESPACE_DNS, source_type + ':' + record_id)."""
    if not source_type:
        raise ValueError("source_type must be a non-empty string")
    if not record_id:
        raise ValueError("record_id must be a non-empty string")
    return _uuid.uuid5(_uuid.NAMESPACE_DNS, f"{source_type}:{record_id}")


def _get_id_field(weaviate_cfg: WeaviateConfig) -> str:
    """The id field name used to extract record_id from the dict.

    Convention: the id field is whichever element of metadata_fields named 'id' or
    ending in '_id'; falls back to the first metadata_field. This mirrors how the
    SyncEngine (Phase 4) will pull record_id from records — kept loose here so the
    upsert function works without a SourceConfig dependency.
    """
    for field in weaviate_cfg.metadata_fields:
        if field == "id" or field.endswith("_id"):
            return field
    if weaviate_cfg.metadata_fields:
        return weaviate_cfg.metadata_fields[0]
    raise ValueError(
        "weaviate.metadata_fields is empty; cannot determine record_id field. "
        "Add 'id' (or an *_id field) to metadata_fields in config.yaml."
    )


def _coerce_value(field_name: str, value: Any) -> Any:
    """Coerce a record value into a Weaviate-acceptable JSON-serializable form.

    Returns None if the value should be skipped (Weaviate accepts missing properties).
    """
    if value is None or value == "":
        return None

    # NUMBER fields: coerce strings to float so Weaviate's NUMBER property accepts them.
    name = field_name.lower()
    if name in _NUMBER_FIELDS or any(name.endswith(s) for s in _NUMBER_SUFFIXES):
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    # DATE fields (heuristic matches schema.py): ensure timezone-aware ISO-8601.
    if field_name.endswith("_at") or field_name in {"created", "updated"}:
        if isinstance(value, _dt.datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=_dt.timezone.utc)
            return value.isoformat()
        if isinstance(value, str):
            s = value.strip()
            if "T" in s or " " in s:
                # Datetime string — append Z (UTC) only if no timezone marker present.
                # Date-only strings (e.g. "2024-01-15") are returned as-is: appending
                # Z would produce "2024-01-15Z" which is not a valid ISO-8601 datetime.
                if not (s.endswith("Z") or "+" in s[10:] or s[10:].count("-") > 0):
                    s = s + "Z"
            return s
        return str(value)

    return value


def _filter_properties(
    record: dict[str, Any],
    allowed_fields: set[str],
) -> dict[str, Any]:
    """Keep only allowed_fields from the record; coerce values; drop None."""
    out: dict[str, Any] = {}
    for field in allowed_fields:
        if field in _WEAVIATE_RESERVED_PROPS:
            continue
        if field not in record:
            continue
        coerced = _coerce_value(field, record[field])
        if coerced is None:
            continue
        out[field] = coerced
    return out


def _build_document(record: dict[str, Any], text_fields: list[str]) -> str:
    """Concatenate text_fields values into a single string for embedding."""
    parts = [str(record[f]) for f in text_fields if record.get(f) not in (None, "")]
    return " ".join(parts)


def upsert_records(
    client,
    records: list[dict[str, Any]],
    weaviate_cfg: WeaviateConfig,
    source_type: str,
    embedding_adapter=None,
    id_field: str | None = None,
    start_from_batch: int = 0,
    on_batch_done: Callable[[int, int, int], None] | None = None,
) -> UpsertResult:
    """Idempotently upsert each record into the collection.

    Processes records in batches of _EMBED_BATCH_SIZE. For each batch:
      1. Embed the batch (if embedding_adapter provided)
      2. Upsert each record in the batch to Weaviate
      3. Call on_batch_done(batch_num, done_records, total_records)

    start_from_batch: skip batches 0..N-1 (already processed in a previous run).
    on_batch_done: called after each batch completes embed+upsert — use for
                   checkpoint writes and progress reporting.

    RAM usage: O(batch_size * dims) instead of O(total * dims) — ~2 MB per batch
    instead of ~4 GB for 1M records at 1024 dims.
    """
    if not records:
        logger.info("upsert_records called with empty list; nothing to do.")
        return UpsertResult(0, 0, 0)

    collection_obj = client.collections.get(weaviate_cfg.collection)
    if id_field is None:
        id_field = _get_id_field(weaviate_cfg)
    allowed = set(weaviate_cfg.text_fields) | set(weaviate_cfg.metadata_fields)

    total = len(records)
    total_batches = (total + _EMBED_BATCH_SIZE - 1) // _EMBED_BATCH_SIZE
    inserted = updated = skipped = 0

    if start_from_batch > 0:
        logger.info(
            "Riprendendo da batch %d/%d (%d record già processati).",
            start_from_batch, total_batches, start_from_batch * _EMBED_BATCH_SIZE,
        )

    for batch_num in range(total_batches):
        i = batch_num * _EMBED_BATCH_SIZE
        batch_records = records[i: i + _EMBED_BATCH_SIZE]

        if batch_num < start_from_batch:
            continue  # already embedded+upserted in a previous run

        # --- Embed batch -------------------------------------------------------
        batch_vecs: list[list[float] | None]
        if embedding_adapter is not None:
            batch_docs = [_build_document(r, weaviate_cfg.text_fields) for r in batch_records]
            batch_vecs = embedding_adapter.embed(batch_docs)
        else:
            batch_vecs = [None] * len(batch_records)

        # --- Upsert batch ------------------------------------------------------
        for j, record in enumerate(batch_records):
            raw_id = record.get(id_field)
            if raw_id is None or raw_id == "":
                logger.warning("Record missing id field %r; skipping.", id_field)
                skipped += 1
                continue
            record_id = str(raw_id)
            obj_uuid = compute_record_uuid(source_type, record_id)
            properties = _filter_properties(record, allowed)
            vector = batch_vecs[j]

            try:
                if collection_obj.data.exists(uuid=obj_uuid):
                    collection_obj.data.replace(uuid=obj_uuid, properties=properties, vector=vector)
                    updated += 1
                else:
                    collection_obj.data.insert(properties=properties, uuid=obj_uuid, vector=vector)
                    inserted += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("Upsert failed for record_id=%r uuid=%s: %s", record_id, obj_uuid, exc)
                skipped += 1

        done = min(i + _EMBED_BATCH_SIZE, total)
        logger.info(
            "Batch %d/%d completato — record: %d/%d (ins=%d upd=%d skip=%d)",
            batch_num + 1, total_batches, done, total, inserted, updated, skipped,
        )
        if on_batch_done is not None:
            on_batch_done(batch_num, done, total)

    result = UpsertResult(inserted=inserted, updated=updated, skipped=skipped)
    logger.info(
        "upsert_records done: inserted=%d updated=%d skipped=%d total_processed=%d",
        result.inserted, result.updated, result.skipped, result.total,
    )
    return result
