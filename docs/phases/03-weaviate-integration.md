# Phase 03 — Weaviate Integration

Integrates the Weaviate vector database: automatic schema creation, record upsert with vectors, and embedding model change detection.

## What was built

### Automatic schema creation

On startup, the orchestrator calls `create_collection_if_missing()`. If the Weaviate collection does not exist yet, it is created automatically based on `config.yaml`:

- `text_fields` → indexed as `text` (vectorized)
- `metadata_fields` → stored as typed properties (not vectorized)
- Weaviate is configured with `DEFAULT_VECTORIZER_MODULE: none` — vectors are always passed explicitly by the orchestrator

Weaviate reserves `id` and `vector` as internal property names. Any field with these names is automatically skipped from schema creation and upsert.

### Record upsert

`upsert_records(client, config, records, vectors)` performs idempotent upserts:

- **Deterministic UUID**: `uuid5(NAMESPACE_DNS, source_type + ":" + record_id)` — the same record always maps to the same UUID, making upserts fully idempotent regardless of how many times they are run
- Batch upsert via Weaviate client v4's batch API
- Vectors are pre-computed by the embedding adapter and passed as the `vector=` kwarg

### Model version detection

`model_version.py` persists the active embedding model name in `/app/.sync/model_version.json`. On startup:

1. Load the saved model name
2. Compare with `embedding.model` in current config
3. If different → trigger automatic full re-index before any sync

This ensures that stored vectors are always consistent with the current embedding model.

### Weaviate client

A singleton Weaviate v4 client is created at startup via `weaviate_store/client.py`. The client connects to `WEAVIATE_URL` from environment (default: `http://vector-db:8080`).

> Weaviate v4 client requires server version ≥ 1.27. The `docker-compose.yml` uses `semitechnologies/weaviate:1.27.2`.

## Key files

| File | Purpose |
|---|---|
| `sync-service/weaviate_store/client.py` | Singleton Weaviate v4 client |
| `sync-service/weaviate_store/schema.py` | `create_collection_if_missing()` |
| `sync-service/weaviate_store/upsert.py` | `upsert_records()` + `compute_record_uuid()` |
| `sync-service/weaviate_store/model_version.py` | Model change detection + auto re-index trigger |
