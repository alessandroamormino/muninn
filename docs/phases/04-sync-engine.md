# Phase 04 — Sync Engine

Implements the core sync logic: incremental hash-based sync, state persistence, and the `/sync` REST endpoints.

## What was built

### SyncEngine

`sync/engine.py` implements two sync modes:

**`run_full()`** — Full re-index:
1. Drop and recreate the Weaviate collection
2. Clear all saved hash state
3. Fetch all records from the source adapter
4. Embed in batches and upsert everything

**`run_incremental()`** — Incremental sync:
1. Fetch all records from the source adapter
2. For each record, compute MD5 hash over `sync.hash_fields`
3. Compare against saved hashes in `StateStore`
4. Upsert only new or changed records
5. Update `StateStore` with new hashes

### StateStore

`sync/state_store.py` persists record hashes to survive container restarts:
- Storage path: `/app/.sync/sync_state.json` (mounted Docker volume `sync_data`)
- Atomic writes: write to temp file, then `os.replace()` — no partial writes on crash
- Thread-safe load: handles missing or corrupt file gracefully

### REST endpoints

`api/sync.py` exposes:

| Endpoint | Description |
|---|---|
| `POST /sync?collection=X` | Trigger incremental sync (non-blocking) |
| `POST /sync/full?collection=X` | Trigger full re-index (non-blocking) |
| `GET /sync/status?collection=X` | Get last sync result |

Both `POST` endpoints run the sync in the background via FastAPI `BackgroundTasks` and return immediately with `202 Accepted`. A boolean lock (`app.state.sync_lock`) prevents concurrent syncs — a second request while a sync is in progress returns `409 Conflict`.

### Concurrency model

A simple boolean lock is used (not thread-safe beyond Python's GIL). This is sufficient for single-instance deployments where concurrent syncs would conflict at the Weaviate level anyway. Production multi-worker deployments should replace this with a distributed lock (Redis, database).

## Key files

| File | Purpose |
|---|---|
| `sync-service/sync/engine.py` | `SyncEngine` with `run_full()` and `run_incremental()` |
| `sync-service/sync/state_store.py` | Hash persistence with atomic writes |
| `sync-service/api/sync.py` | `/sync`, `/sync/full`, `/sync/status` endpoints |
