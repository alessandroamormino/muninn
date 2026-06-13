---
phase: 25-openai-embedding-adapter
plan: "02"
subsystem: embeddings
tags: [openai, batch-api, embedding, precomputed-adapter, tdd, engine-branching]
dependency_graph:
  requires: [25-01]
  provides: [OpenAI-Batch-API-path, _PrecomputedAdapter, embed_batch_async]
  affects: [sync-service/embeddings/openai_adapter.py, sync-service/sync/engine.py]
tech_stack:
  added: []
  patterns: [batch-api-checkpoint, precomputed-adapter-pattern, engine-branching-supports-batch-api]
key_files:
  created:
    - sync-service/tests/test_openai_batch.py
    - sync-service/tests/test_engine_batch_path.py
  modified:
    - sync-service/embeddings/openai_adapter.py
    - sync-service/sync/engine.py
decisions:
  - Option B (_PrecomputedAdapter) chosen over Option A (index_records kwarg) — no interface change to BaseVectorStore
  - Checkpoint for Batch API written BEFORE polling so process restart can resume (Pitfall 7)
  - 50K-input cap enforced with clear error message — single-batch path only for Phase 25
  - _BATCH_CHECKPOINT_DIR=/app/.sync mirrors existing sync/checkpoint.py _CHECKPOINT_DIR
metrics:
  duration: "25 minutes"
  completed: "2026-06-13"
  tasks: 2
  files_modified: 4
---

# Phase 25 Plan 02: OpenAI Batch API Path Summary

OpenAI Batch API path added to `OpenAIEmbeddingAdapter`: JSONL build, upload, poll, download, reorder by `custom_id`, resume-from-checkpoint — wired into `SyncEngine.run_full()` via `supports_batch_api` property detection.

## What Was Built

### Task 1: Extend OpenAIEmbeddingAdapter with Batch API path (commits: 1696e85, 6852d3e)

Extended `sync-service/embeddings/openai_adapter.py` with:

**New constants:**
- `_BATCH_POLL_INTERVAL = 30.0` — seconds between polls
- `_BATCH_MAX_INPUTS = 50_000` — OpenAI Batch API per-request cap
- `_BATCH_CHECKPOINT_DIR = pathlib.Path("/app/.sync")` — checkpoint directory
- `_BATCH_TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}`

**New property on `OpenAIEmbeddingAdapter`:**
- `supports_batch_api` — returns `bool(self._cfg.openai_batch)`. Added `self._cfg = embedding_cfg` to `__init__` to persist config.

**New method on `OpenAIEmbeddingAdapter`:**
- `embed_batch_async(texts, *, collection_name)` — full Batch API flow:
  1. Empty input → return `[]`
  2. `len(texts) > 50,000` → `OpenAIEmbeddingError` with 50K cap message
  3. Check `_read_batch_checkpoint(collection_name)` — if found, resume polling
  4. Build JSONL via `_build_jsonl()`, upload via `client.files.create(purpose="batch")`
  5. Submit via `client.batches.create(endpoint="/v1/embeddings", completion_window="24h")`
  6. Write checkpoint via `_write_batch_checkpoint()` BEFORE polling
  7. Poll via `_poll_batch()` — max 2880 iterations (24h cap)
  8. On non-"completed" terminal status → raise `OpenAIEmbeddingError`
  9. Download via `client.files.content()`, parse via `_parse_batch_output()`
  10. Delete checkpoint on success

**Module-level helpers (unit-testable):**
- `_build_jsonl(texts, model)` — builds UTF-8 JSONL bytes with correct Batch API format
- `_parse_batch_output(content_text, n_inputs)` — handles SDK content object or plain string; extracts via `obj["response"]["body"]["data"][0]["embedding"]` (Pitfall 5); validates count; reorders by `custom_id`
- `_write_batch_checkpoint(collection_name, batch_id, input_file_id)` — writes JSON checkpoint
- `_read_batch_checkpoint(collection_name)` — reads checkpoint or returns None
- `_delete_batch_checkpoint(collection_name)` — removes checkpoint file on success
- `_poll_batch(client, batch_id, masked_key, sleep_fn=time.sleep)` — polls until terminal status with 24h cap; `sleep_fn` injected for test speed

**Security:** raw API key never in any log or exception — only `self._masked_key`.

**Created `sync-service/tests/test_openai_batch.py`** with 15 tests across 6 classes:

| Class | Tests | Coverage |
|-------|-------|----------|
| `TestSupportsBatchApiProperty` | 2 | Property reflects openai_batch flag |
| `TestBuildJsonl` | 1 | JSONL format: custom_id, method, url, body fields |
| `TestParseBatchOutput` | 3 | Reorder by custom_id; count mismatch error; SDK object + plain string |
| `TestBatchFullFlow` | 4 | Full flow; checkpoint write/delete; empty input; 50K cap |
| `TestBatchFailureModes` | 3 | failed/expired/cancelled terminal statuses raise with masked key |
| `TestBatchResume` | 2 | Resume from checkpoint; expired checkpoint error with delete instruction |

### Task 2: Wire OpenAI Batch API path into SyncEngine.run_full (commits: d634975, 83d51c2)

**Added `_PrecomputedAdapter` class to `sync-service/sync/engine.py`:**

```python
class _PrecomputedAdapter:
    def embed(self, texts): # slices precomputed vectors by cursor
    def dimensions(self):   # forwards to real adapter
    def model_name(self):   # forwards to real adapter
```

Chosen over Option A (`precomputed_vectors` kwarg to `index_records`) because it avoids modifying the `BaseVectorStore` interface. The adapter wraps the precomputed vector list and advances a cursor with each `embed()` call, matching how `index_records` calls the adapter in order.

**Modified `SyncEngine.run_full()`:**

```python
use_batch_api = getattr(self._embedding_adapter, "supports_batch_api", False)
self._vector_store.begin_bulk_load(collection_name, mode)
try:
    if use_batch_api:
        # Batch API path: full load → embed_batch_async → single index_records
        records = self._source_adapter.fetch_records()
        texts = [" ".join(...) for r in records]
        vectors = self._embedding_adapter.embed_batch_async(texts, collection_name=...)
        precomputed_adapter = _PrecomputedAdapter(vectors, self._embedding_adapter)
        result = self._vector_store.index_records(records, ..., precomputed_adapter, ...)
    else:
        # Streaming path: unchanged for all non-batch adapters
        for chunk in self._source_adapter.fetch_records_chunked(...):
            ...
finally:
    self._vector_store.end_bulk_load(collection_name)
```

Both branches inside `begin_bulk_load`/`end_bulk_load` try/finally (HNSW staging preserved).
State persistence, checkpoint delete, and model_version write execute after both branches.

**Created `sync-service/tests/test_engine_batch_path.py`** with 2 tests in `TestEngineBatchIntegration`:
- `test_run_full_uses_batch_path_when_supports_batch_api` — verifies `fetch_records()` called once, `fetch_records_chunked` not called, `embed_batch_async` called once with correct `collection_name`, `index_records` called once
- `test_run_full_uses_streaming_when_supports_batch_api_false` — verifies streaming path unchanged

## Test Results

- `python3 -m pytest tests/test_openai_batch.py -q` — **15 passed**
- `python3 -m pytest tests/test_engine_batch_path.py -q` — **2 passed**
- `python3 -m pytest tests/ -q` — **38 passed** (21 from Plan 25-01 + 17 new)
- **0 regressions** vs Plan 25-01 baseline

## Implementation Decisions

### Option B: `_PrecomputedAdapter` (chosen over Option A: `index_records` kwarg)

**Option A** would add a `precomputed_vectors` parameter to `BaseVectorStore.index_records()`. This requires modifying the abstract interface plus both concrete implementations (`WeaviateVectorStore`, `QdrantVectorStore`). More files, more surface area.

**Option B** (chosen) adds a thin wrapper class in `engine.py` that presents the precomputed vectors as if it were a live embedding adapter. `index_records` calls `embed()` per batch in order — the wrapper advances a cursor, serving the correct slice each time. Zero changes to `BaseVectorStore` or either store implementation.

## Known Limitations

1. **50K-input cap** — The Batch API enforces a maximum of 50,000 requests per batch file. For datasets larger than 50K records, `embed_batch_async` raises `OpenAIEmbeddingError` with a clear message instructing the user to use the sync path (`openai_batch: false`) or split the dataset manually. A multi-batch concurrent submission path (submit 30 batches of 50K each for 1.5M records, poll all, reassemble) is deferred.

2. **RAM usage** — The batch path loads ALL records into memory at once (via `fetch_records()`), then builds all text strings before submitting the JSONL. For 1.5M records with ~100 bytes/text average, this is ~150 MB for texts alone plus record metadata. Acceptable for the stated use case (one-time bulk load), but the log message warns users.

3. **Checkpoint scope** — The `.sync/{collection}.batch_checkpoint.json` checkpoint stores only `batch_id` and `input_file_id`. If the process restarts after the checkpoint is written but before the batch completes, it resumes polling the existing job. However, if the batch `expires` (24h TTL), the user must manually delete the checkpoint file and retry — this is logged clearly in the error message.

## Notes for End-to-End UAT

Suggested flow to validate the Batch API path in Docker:

1. Set `OPENAI_API_KEY=sk-...` in `.env`
2. In a per-entity `configuration/YourEntity/config.yaml`, set:
   ```yaml
   embedding:
     type: openai
     model: text-embedding-3-small
     api_key: ${OPENAI_API_KEY}
     openai_batch: true
   ```
3. Run `docker-compose up -d` (Qdrant + orchestrator, no Ollama needed for this path)
4. `POST /sync/full` — observe logs:
   - `"OpenAI Batch API path active for 'YourEntity' — loading all records upfront"`
   - `"OpenAI batch submitted (batch_id=batch_xxx, file_id=file_yyy, n_inputs=N)"`
   - Poll logs every 30s: `"OpenAI batch batch_xxx status=in_progress (0/N completed)"`
   - `"OpenAI batch batch_xxx completed; downloaded N embeddings"`
5. `GET /search?q=test` — verify semantically relevant results returned

Note: the batch job takes minutes to hours depending on dataset size and tier. For a small test (< 100 records), consider using `openai_batch: false` for UAT speed.

## Deviations from Plan

None — plan executed exactly as written.

- Option B (`_PrecomputedAdapter`) was the specified implementation choice in the plan; implemented as specified.
- `supports_batch_api` uses `getattr(self._cfg, "openai_batch", False)` with `bool()` wrapping as specified.
- `embed_batch_async` resume path checks checkpoint at start, retrieves batch, handles `expired` specially as specified.

## Known Stubs

None — all behaviors are fully wired.

## Threat Flags

No new security surface introduced beyond what was analyzed in the plan's threat model (T-25-02-01 through T-25-02-SC). All mitigations applied:
- T-25-02-02 (key masking in logs): raw key never in any log or exception (verified by grep gate)
- T-25-02-04 (polling loop cap): `for _ in range(2880)` cap = 24h implemented in `_poll_batch`
- T-25-02-06 (batch output reorder): `_parse_batch_output` validates count and reorders by `custom_id`

## Self-Check: PASSED

- `sync-service/embeddings/openai_adapter.py` — FOUND (with supports_batch_api, embed_batch_async, helpers)
- `sync-service/sync/engine.py` — FOUND (with _PrecomputedAdapter, supports_batch_api branch in run_full)
- `sync-service/tests/test_openai_batch.py` — FOUND (15 tests, 6 classes)
- `sync-service/tests/test_engine_batch_path.py` — FOUND (2 tests, 1 class)
- Commit 1696e85 (test RED: batch tests) — verified
- Commit 6852d3e (feat: Batch API implementation) — verified
- Commit d634975 (test RED: engine batch tests) — verified
- Commit 83d51c2 (feat: engine branching) — verified
