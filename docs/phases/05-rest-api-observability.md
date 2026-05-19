# Phase 05 — REST API & Observability

Implements the search endpoint, health check, info endpoint, and exposes the Swagger UI.

## What was built

### Search endpoint

`GET /search` performs a vector similarity search using the Ollama embedding adapter:

1. Embed the query string client-side via Ollama
2. Pass the vector to Weaviate's `near_vector` query
3. Apply optional field projection and result limit
4. Return JSON with matching records

> In Phase 07 this was upgraded to **hybrid search** (BM25 + semantic). See [Phase 07 docs](07-hybrid-search.md).

### Health check

`GET /health` — probes Weaviate connectivity:

```json
// 200 OK
{"status": "ok"}

// 503 Service Unavailable
{"status": "weaviate_unreachable"}
```

Useful for container health checks and load balancer probes.

### Info endpoint

`GET /info?collection=X` — returns runtime metadata:

```json
{
  "collection": "Products",
  "total_objects": 5432,
  "embedding_model": "qwen3-embedding:4b",
  "embedding_type": "ollama"
}
```

### Swagger UI

FastAPI's built-in OpenAPI docs are available at `/docs`. All endpoints, parameters, and response schemas are auto-documented.

## Key files

| File | Purpose |
|---|---|
| `sync-service/api/search.py` | `GET /search` endpoint |
| `sync-service/main.py` | `GET /health`, `GET /info` + FastAPI app setup |
