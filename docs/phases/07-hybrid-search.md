# Phase 07 — Hybrid Search, Structured Filters & Response Times

Upgrades `GET /search` from pure semantic search to hybrid BM25 + semantic search, adds structured filters, and adds `took_ms` to responses.

## What was built

### Hybrid search

`GET /search` now uses Weaviate's `hybrid()` query instead of `near_vector()`:

- **Semantic component**: query is embedded client-side via Ollama and passed as `vector=`
- **BM25 component**: Weaviate's built-in full-text index
- **Alpha = 0.5**: equal weight between the two — finds both exact names/codes and conceptual matches

**Why hybrid?** Pure semantic search struggles with exact strings like company names or product codes. A query like "BianchiTech senior engineers" finds the company name via BM25 and the role via semantics.

Response now returns `_score` (higher = better) instead of `_distance`.

### Structured filters

`?filter=Field:Value[,Field2:Value2]` adds exact-match conditions on top of the semantic query:

```bash
curl "http://localhost:8000/search?q=engineer&collection=Employees&filter=Department:Engineering,Seniority:Senior"
```

Rules:
- Only `metadata_fields` are filterable — requesting a `text_field` returns `422`
- Filter uses `Filter.by_property().like()` — Weaviate-level filter
- Values may contain `:` — only the **first** colon is the separator (`City:New York` works)
- Weaviate automatically lowercases the first letter of property names — filter field names are normalized internally

### Response time tracking

`took_ms` (integer, milliseconds) is added to:
- `GET /search` response
- `GET /sync/status` `last_run` object (both success and failure paths)

Time is measured from the start of the search/sync operation to completion.

### `min_score` filter

`?min_score=0.6` filters out results below the threshold:

```bash
curl "http://localhost:8000/search?q=python+developer&min_score=0.65"
```

Records without meaningful text content (e.g., missing description fields) tend to score ~0.5 from Weaviate — `?min_score=0.51` effectively excludes them.

## Key files

| File | Purpose |
|---|---|
| `sync-service/api/search.py` | Hybrid query, filter parsing, min_score, took_ms |
