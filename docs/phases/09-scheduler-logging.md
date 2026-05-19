# Phase 09 — Scheduler & Sync Logging

Adds cron-based automatic sync scheduling and a persistent sync history log.

## What was built

### Automatic scheduler

`scheduler.py` wraps [APScheduler](https://apscheduler.readthedocs.io/) to run syncs on a cron schedule configured in `config.yaml`:

```yaml
sync:
  schedule: "0 */6 * * *"   # every 6 hours
  # or:
  schedule: manual           # no automatic sync
```

The scheduler is started at application startup via FastAPI's `lifespan` handler. If `schedule: manual` (or no schedule), APScheduler is not started — `build_scheduler()` lazy-imports the library so it is not required when unused.

**Concurrency**: the scheduled job calls `sync_lock.acquire(blocking=False)`. If a manual sync is already running, the scheduled job logs `"skipped"` and exits without waiting.

### Sync history (LogStore)

`sync/log_store.py` persists sync history in a SQLite database (`/app/.sync/sync_log.db`):

- **WAL mode**: allows concurrent reads during writes
- **`check_same_thread=False`**: safe for FastAPI's async context
- **Auto-prune**: keeps the last 1000 entries per collection on each insert

Each log entry records:
- Collection name
- Sync type: `full`, `incremental`, or `scheduled`
- Status: `success` or `error`
- Records synced
- Duration in milliseconds
- Trigger: `manual` or `scheduler`
- Timestamp

### `GET /logs` endpoint

```bash
curl "http://localhost:8000/logs?collection=Products&limit=20"
```

Returns an array of sync history entries, newest first.

## Key files

| File | Purpose |
|---|---|
| `sync-service/scheduler.py` | APScheduler setup + `build_scheduler()` factory |
| `sync-service/sync/log_store.py` | SQLite-backed sync history |
| `sync-service/api/logs.py` | `GET /logs` endpoint |
