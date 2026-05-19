# Phase 10 — File Upload API

Adds a file upload endpoint and per-collection configuration management, enabling the web GUI to create and update collections without manual file editing.

## What was built

### `POST /upload`

Upload a CSV or JSON file for a collection and optionally trigger a sync:

```bash
curl -X POST http://localhost:8000/upload \
  -F "file=@./data/products.csv" \
  -F "collection=Products"
```

The uploaded file is saved to `./data/<filename>` (mounted volume) and the collection's `config.yaml` is updated to point to it.

### Per-collection config write API

The upload endpoint uses `_write_config()` — a shared utility that writes or updates a `configuration/<Collection>/config.yaml` file. This same utility is used by the web GUI's Settings page to create new collections and update existing ones.

The function accepts keyword-only arguments:

```python
_write_config(
  collection="Products",
  source={"type": "csv", "file_path": "./data/products.csv", ...},
  text_fields=["description", "name"],
  metadata_fields=["price", "category"],
  output_fields=["name", "description", "price"],
)
```

### `POST /sync/full/by-collection`

Triggers a full re-index for a collection by name, used by the GUI after uploading a new file or changing configuration.

### Security

- Collection names are validated against `_COLLECTION_RE = r"^[a-zA-Z0-9_-]+$"` before any file or Weaviate I/O — prevents path traversal attacks
- API keys and credentials are never stored or transmitted by the GUI — only the env var name (e.g. `MY_API_KEY`) is sent; the backend writes `${MY_API_KEY}` as a placeholder in config

## Key files

| File | Purpose |
|---|---|
| `sync-service/api/upload.py` | `POST /upload` + `_write_config()` + `POST /sync/full/by-collection` |
