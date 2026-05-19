# Phase 02 — Source & Embedding Adapters

Implements the first concrete source adapters (CSV, JSON) and the default embedding adapter (Ollama).

## What was built

### CSVAdapter

Reads local CSV files. Features:
- Configurable delimiter (`delimiter: ","` or `";"`)
- Column name normalization: spaces → underscores (`Job Title` → `Job_Title`)
- ID-based record identification via `id_field`
- MD5 hash over `hash_fields` for change detection
- Numeric coercion: fields matching known patterns (`price`, `cost`, `_count`, `_rate`, etc.) are converted to `float` before indexing

Configuration:
```yaml
source:
  type: csv
  file_path: ./data/products.csv
  id_field: sku
  delimiter: ";"
```

### JSONAdapter

Reads JSON from a local file or a remote URL. Features:
- Optional `json_key` to unwrap a nested array (`{"results": [...]}`)
- Bearer token substitution from `.env` for authenticated URLs
- `AdapterError` on network failure (wrapped, not propagated raw)

Configuration:
```yaml
source:
  type: json
  file_path: ./data/items.json   # or https://api.example.com/items.json
  id_field: id
  json_key: results
```

### OllamaEmbeddingAdapter

Generates embeddings via a locally running [Ollama](https://ollama.com/) instance. Features:
- Calls `POST /api/embed` on the Ollama server
- Batch processing: 100 records per request to avoid timeouts on large datasets
- 300s request timeout
- Any Ollama-compatible model (default: `qwen3-embedding:4b` — 2560 dimensions, multilingual, offline)

Configuration:
```yaml
embedding:
  type: ollama
  model: qwen3-embedding:4b
  endpoint: http://host.docker.internal:11434
```

On macOS, Ollama runs natively (not in Docker) to access the Metal GPU. On Linux, Ollama can run as a Docker container.

### Adapter factories

`sources/__init__.py` exports `build_source_adapter(config)` — instantiates the correct adapter from `source.type` in config.

`embeddings/__init__.py` exports `build_embedding_adapter(config)` — instantiates the correct adapter from `embedding.type`.

Adding a new source type:
1. Create `sync-service/sources/my_adapter.py` implementing `BaseSourceAdapter`
2. Add a branch to `build_source_adapter()` in `sources/__init__.py`
3. No other files need to change

## Key files

| File | Purpose |
|---|---|
| `sync-service/sources/csv_adapter.py` | CSVAdapter |
| `sync-service/sources/json_adapter.py` | JSONAdapter |
| `sync-service/sources/__init__.py` | `build_source_adapter` factory |
| `sync-service/embeddings/ollama_adapter.py` | OllamaEmbeddingAdapter |
| `sync-service/embeddings/__init__.py` | `build_embedding_adapter` factory |
