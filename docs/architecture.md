# Architecture

smart-search is built around a **Plugin/Adapter Pattern**: every data source, every embedding model, and every vector store is a swappable module behind a common interface. Adding a new source or model requires a single new file — no changes to the core.

For a visual call-flow diagram, open [`graphify-out/smart-search-callflow.html`](../graphify-out/smart-search-callflow.html) in a browser.

---

## System layers

```
┌─────────────────────────────────────────────────────────────┐
│                        sync-service (FastAPI)                │
│                                                              │
│  ┌──────────────────────── SOURCE ADAPTERS ───────────────┐  │
│  │  CSV │ JSON │ REST API │ MySQL │ ...                   │  │
│  │  all implement BaseSourceAdapter                        │  │
│  └────────────────────────┬───────────────────────────────┘  │
│                           │ normalised records                │
│                           ▼                                   │
│  ┌──────────────── SYNC ENGINE (incremental) ─────────────┐  │
│  │  fetch → hash diff → checkpoint → upsert               │  │
│  └────────────────────────┬───────────────────────────────┘  │
│                           │ texts to embed                    │
│                           ▼                                   │
│  ┌─────────────────── EMBEDDING ADAPTERS ─────────────────┐  │
│  │  Ollama │ WeaviateBuiltin │ ...                         │  │
│  │  all implement BaseEmbeddingAdapter                     │  │
│  └────────────────────────┬───────────────────────────────┘  │
│                           │ float vectors                     │
│                           ▼                                   │
│  ┌──────────────────── VECTOR STORE ──────────────────────┐  │
│  │  QdrantVectorStore │ WeaviateVectorStore                │  │
│  │  both implement BaseVectorStore                         │  │
│  └────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
           ▲
           │ REST API
           ▼
┌──────────────────────┐
│  frontend (React SPA) │  port 3000, Nginx proxy to :8000
└──────────────────────┘
```

---

## Core abstractions

### BaseSourceAdapter (`sources/base.py`)

Every data source implements four methods:

```python
class BaseSourceAdapter:
    def fetch_records(self) -> list[dict]: ...
    def fetch_new_records(self, since: datetime) -> list[dict]: ...
    def get_record_id(self, record: dict) -> str: ...
    def get_record_hash(self, record: dict) -> str: ...
```

Current implementations: `CSVAdapter`, `JSONAdapter`, `RestAPIAdapter`, `MySQLAdapter`.

### BaseEmbeddingAdapter (`embeddings/base.py`)

```python
class BaseEmbeddingAdapter:
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def dimensions(self) -> int: ...
    def model_name(self) -> str: ...
```

Current implementations: `OllamaEmbeddingAdapter` (default), `WeaviateBuiltinAdapter`.

### BaseVectorStore (`vector_stores/base.py`)

```python
class BaseVectorStore:
    def create_index(self, config, embedding_adapter) -> IndexResult: ...
    def search(self, query, vector, config, mode) -> list[SearchHit]: ...
    def upsert(self, records, vectors, config) -> None: ...
    def delete(self, record_ids, config) -> None: ...
    def count(self, config) -> int: ...
```

Current implementations: `QdrantVectorStore` (default), `WeaviateVectorStore`.

---

## Sync pipeline (detail)

```
Source.fetch_records()
       │
       ▼
SyncEngine: compute hash per record (MD5 of hash_fields)
       │
       ├── hash unchanged? → skip
       └── hash changed or new? →
              │
              ▼
       EmbeddingAdapter.embed(texts)   ← batched, 1000 records/batch
              │                           dedup: same text → embed once
              ▼
       VectorStore.upsert(records, vectors)
              │
              ▼
       StateStore.bulk_set(hashes)     ← persist new hashes to SQLite
       checkpoint.write(offset)        ← crash-safe resume point
```

**Model change detection**: on startup, `model_version.py` reads `model_version.json` from the data directory. If the model name changed, it automatically triggers a full re-index before accepting any queries.

**Quantization**: for large collections (>100K records), configure `weaviate.quantization: pq` or `sq` in `config.yaml` to reduce RAM usage at a small quality cost. Must be set before the first index creation — cannot be added to an existing collection.

---

## Search pipeline (detail)

```
GET /search?q=...
       │
       ▼
_parse_negation(q)          ← extract -terms from query
_expand_query(q)            ← replace terms with synonyms if configured
       │
       ▼
EmbeddingAdapter.embed([q]) ← compute query vector
       │
       ▼
VectorStore.search(q, vector, mode)
  │  Qdrant:   sparse (BM25) + dense (vector) hybrid
  │  Weaviate: hybrid(alpha=0.5) — BM25 + near_vector
       │
       ▼
_apply_negation_filter()    ← drop results matching negated terms
filter by min_score         ← drop results below threshold
project output_fields       ← return only configured fields
```

---

## Auth layer

All API endpoints are protected by JWT authentication. The auth layer is in `auth/`:

- `UserStore` — SQLite-backed user registry (bcrypt passwords)
- `RefreshTokenStore` — refresh token management
- `dependencies.py` — FastAPI dependencies: `get_current_user`, `require_admin`

TOTP (two-factor) is supported at login. Anonymous access is disabled by default.

---

## Cache adapter system

The incremental sync uses a pluggable cache to detect which records changed:

| Adapter | Strategy | When to use |
|---|---|---|
| `ExactMatchCacheAdapter` | MD5 hash equality | Default — fast, no false positives |
| `NormalizedCacheAdapter` | Normalise text before hashing | When source data has formatting noise |
| `SemanticCacheAdapter` | Embedding similarity threshold | When records change phrasing but not meaning |

`NormalizedCacheAdapter` and `SemanticCacheAdapter` both delegate exact-match storage to `ExactMatchCacheAdapter` internally.

---

## Docker services

| Service | Image | Port | Notes |
|---|---|---|---|
| `orchestrator` | Python 3.11 + FastAPI | 8000 | The sync-service |
| `vector-db` | Weaviate 1.27.2 | 8080, 50051 | Default vector store |
| `qdrant` | Qdrant latest | 6333, 6334 | Alternative vector store (profile: `qdrant`) |
| `frontend` | Nginx + React build | 3000 | SPA + `/api/*` proxy |
| Ollama | **native on host** | 11434 | Embeddings; Mac: native for Metal GPU |

Start with Qdrant: `COMPOSE_PROFILES=qdrant docker-compose up`  
Start with Weaviate: `docker-compose up` (default)

---

## Extending the system

### Add a new data source

1. Create `sources/mydb_adapter.py`
2. Inherit from `BaseSourceAdapter` and implement the 4 methods
3. Add a `case "mydb":` branch in `sources/__init__.py:build_source_adapter()`
4. Add the config model in `config/settings.py`

### Add a new embedding model

1. Create `embeddings/mymodel_adapter.py`
2. Inherit from `BaseEmbeddingAdapter` and implement `embed()`, `dimensions()`, `model_name()`
3. Add a `case "mymodel":` branch in `embeddings/__init__.py:build_embedding_adapter()`

### Add a new vector store

1. Create `vector_stores/mystore.py`
2. Inherit from `BaseVectorStore` and implement all methods
3. Add it to `VectorStoreConfig` in `config/settings.py` and wire it in `main.py:lifespan()`

---

## Key design decisions

| Decision | Reason |
|---|---|
| Weaviate package renamed to `weaviate_store/` | Avoid Python shadowing the `weaviate-client` PyPI package |
| UUID deterministic: `uuid5(NAMESPACE_DNS, source_type + ":" + id)` | Makes upserts idempotent — same record always gets the same UUID |
| Embedding dedup before batching | Same text appears in multiple records → embed once, reuse vector |
| `min_score` filter is post-query Python | Weaviate/Qdrant don't expose a unified score threshold in hybrid mode |
| Ollama runs native on Mac | Docker on Apple Silicon has no Metal GPU access |
| `window.__on401` global handler in frontend | Centralises JWT expiry handling across all TanStack Query hooks without React Context prop-drilling — documented as a known WR-04 TODO |
