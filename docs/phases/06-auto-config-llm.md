# Phase 06 — Auto-Config via LLM

Adds a `/setup/suggest-config` endpoint that uses a local LLM to suggest a `config.yaml` from a CSV file.

## What was built

### `POST /setup/suggest-config`

Upload a CSV file and receive a suggested configuration:

```bash
curl -X POST http://localhost:8000/setup/suggest-config \
  -F "file=@./data/products.csv"
```

The endpoint:
1. Reads the CSV header and a sample of rows
2. Sends a structured prompt to the local LLM (Ollama, model: `qwen2.5:3b`)
3. Parses the LLM response into a structured config suggestion

**Response:**

```json
{
  "suggested_config": {
    "weaviate": {
      "collection": "Products",
      "text_fields": ["description", "name"],
      "metadata_fields": ["price", "category", "sku", "in_stock"]
    },
    "source": {
      "id_field": "sku"
    }
  }
}
```

### Design notes

- The suggestion is **never auto-applied**. The user reviews it and manually updates `config.yaml`.
- Uses `qwen2.5:3b` (not the embedding model) — loaded on-demand from the same Ollama server.
- The LLM is asked to distinguish text fields (natural language, good for semantic search) from metadata fields (categorical/numeric, good for filters).

## Key files

| File | Purpose |
|---|---|
| `sync-service/api/setup.py` | `POST /setup/suggest-config` endpoint |
| `sync-service/llm/ollama_llm.py` | Ollama LLM client (completion, not embedding) |
