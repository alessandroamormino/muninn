# Phase 01 — Container Foundation

Establishes the Docker stack, project configuration, and FastAPI skeleton.

## What was built

### Docker Compose stack

Two-container setup (three with the web GUI):

- `vector-db` — Weaviate 1.27.2 (open source vector database)
- `orchestrator` — Python 3.11 + FastAPI (sync service and search API)
- `frontend` — Nginx + React SPA (web GUI, added in Phase 11)

Single command to start everything: `docker-compose up`

### Configuration system

All runtime configuration lives in a single `config.yaml` per collection. No code changes required to switch data sources, embedding models, or collections.

```yaml
source:      # where data comes from
embedding:   # which model to use
weaviate:    # collection name, which fields to embed
sync:        # full vs incremental, hash fields, schedule
api:         # output fields, limits
```

Sensitive credentials (API keys, DB passwords) stay in `.env` (gitignored) and are referenced via `${VAR_NAME}` in config.

### Plugin/Adapter Pattern

The architecture enforces two extension points:

**`BaseSourceAdapter`** — implement to add a new data source:
```python
def fetch_records(self) -> list[dict]
def fetch_new_records(self, since: datetime) -> list[dict]
def get_record_id(self, record: dict) -> str
def get_record_hash(self, record: dict) -> str
```

**`BaseEmbeddingAdapter`** — implement to add a new embedding model:
```python
def embed(self, texts: list[str]) -> list[list[float]]
def dimensions(self) -> int
def model_name(self) -> str
```

Adding a new source or model requires only one new file — the core `SyncEngine` is never modified.

### Architecture diagram

```
┌─────────────────────────────────────────────────────┐
│                    Sync Service                      │
│                                                      │
│  ┌─────────────── SOURCE ADAPTERS ────────────────┐  │
│  │  CSV │ JSON │ REST API │ MySQL │ MongoDB │ ...  │  │
│  └───────────────────┬────────────────────────────┘  │
│                      │ normalized records             │
│                      ▼                               │
│  ┌──────────── SYNC ENGINE ───────────────────────┐  │
│  │  hash check → upsert → save state             │  │
│  └───────────────────┬────────────────────────────┘  │
│                      ▼                               │
│  ┌────────── EMBEDDING ADAPTERS ──────────────────┐  │
│  │  Ollama │ OpenAI │ Cohere │ ...               │  │
│  └───────────────────┬────────────────────────────┘  │
│                      ▼                               │
│  ┌─────────────── Weaviate ───────────────────────┐  │
│  │         vector store + semantic search         │  │
│  └────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

## Key files

| File | Purpose |
|---|---|
| `docker-compose.yml` | Stack orchestration |
| `sync-service/main.py` | FastAPI entrypoint + lifespan |
| `sync-service/config/settings.py` | Pydantic settings, loads `config.yaml` |
| `sync-service/sources/base.py` | `BaseSourceAdapter` ABC |
| `sync-service/embeddings/base.py` | `BaseEmbeddingAdapter` ABC |
| `configuration/config.yaml` | Runtime configuration |
| `.env.example` | Environment variable template |
