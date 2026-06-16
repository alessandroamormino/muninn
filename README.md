# smart-search

Self-hosted semantic search engine — point it at any data source, run `docker-compose up`, get a working `/search` endpoint in minutes. Zero code required.

## What it does

smart-search indexes your data into a local [Weaviate](https://weaviate.io/) vector database using [Ollama](https://ollama.com/) embeddings and exposes a REST API for hybrid semantic + keyword search. A React web GUI lets you manage collections, run syncs, explore logs, and visualize data as a knowledge graph.

```
CSV / JSON / MySQL / REST API  →  sync-service  →  Qdrant / Weaviate  →  GET /search?q=...
                                    (Ollama or OpenAI embeddings)
```

## Quick Start

**Prerequisites:** Docker, Docker Compose, [Ollama](https://ollama.com/) running locally with an embedding model pulled.

```bash
# 1. Pull the embedding model (one-time)
ollama pull qwen3-embedding:4b

# 2. Clone and configure
git clone https://github.com/your-username/smart-search.git
cd smart-search
cp .env.example .env

# 3. Edit configuration/config.yaml to point at your data source
# (see docs/configuration.md for all options)

# 4. Start the stack
docker-compose up

# 5. Trigger a full sync
curl -X POST http://localhost:8000/sync/full

# 6. Search
curl "http://localhost:8000/search?q=your+query"
```

Web GUI: [http://localhost:3000](http://localhost:3000)  
API docs (Swagger): [http://localhost:8000/docs](http://localhost:8000/docs)

## Services

| Service | Container | Default port | Notes |
|---|---|---|---|
| Orchestrator (FastAPI) | `orchestrator` | 8000 | Sync + search API |
| Vector DB (Weaviate) | `vector-db` | 8080 | Default vector store |
| Vector DB (Qdrant) | `qdrant` | 6333 | Optional: `COMPOSE_PROFILES=qdrant docker-compose up` |
| Web GUI (Nginx + React) | `frontend` | 3000 | |
| Embedder (Ollama) | native on host | 11434 | |

> **macOS**: Ollama runs natively to use Metal GPU. On Linux (production), run Ollama as a container and set `OLLAMA_ENDPOINT=http://embedder:11434` in your environment.

## Data Sources

| Source | Type in config.yaml | Status |
|---|---|---|
| CSV file | `csv` | ✅ |
| JSON file / URL | `json` | ✅ |
| REST API (any) | `rest_api` | ✅ |
| MySQL / MariaDB | `mysql` | ✅ flat queries, JOINs, SSL |
| PostgreSQL | `postgresql` | Planned |
| MongoDB | `mongodb` | Planned |

## Search API

```
GET /search?q=<query>
         &limit=10
         &fields=field1,field2
         &filter=Field:Value,Field2:Value2
         &min_score=0.6
```

Returns JSON with `_score` (hybrid BM25 + semantic, higher = better) and `took_ms`.

## Documentation

- [User guide](docs/user-guide.md) — what it does, how to use the GUI, use cases
- [Architecture](docs/architecture.md) — system layers, adapters, how to extend
- [Configuration reference](docs/configuration.md)
- [API reference](docs/api-reference.md)
- [Deployment guide](docs/deployment.md)

### Feature history (by phase)

| Phase | Features |
|---|---|
| [01 — Container Foundation](docs/phases/01-container-foundation.md) | Docker stack, FastAPI, config.yaml |
| [02 — Source & Embedding Adapters](docs/phases/02-source-adapters.md) | CSVAdapter, JSONAdapter, OllamaEmbeddingAdapter, adapter pattern |
| [03 — Weaviate Integration](docs/phases/03-weaviate-integration.md) | Schema creation, upsert, model version detection |
| [04 — Sync Engine](docs/phases/04-sync-engine.md) | Incremental sync, hash diffing, StateStore, `/sync` endpoints |
| [05 — REST API & Observability](docs/phases/05-rest-api-observability.md) | `/search`, `/health`, `/info`, Swagger UI |
| [06 — Auto-Config via LLM](docs/phases/06-auto-config-llm.md) | `/setup/suggest-config` — LLM suggests field mapping from CSV |
| [07 — Hybrid Search](docs/phases/07-hybrid-search.md) | BM25 + semantic, structured filters, `took_ms` |
| [08 — REST API Adapter](docs/phases/08-rest-api-adapter.md) | Generic HTTP source with auth strategies and pagination |
| [09 — Scheduler & Logging](docs/phases/09-scheduler-logging.md) | Cron scheduling, sync history log |
| [10 — File Upload API](docs/phases/10-file-upload.md) | Per-collection config, CSV upload via GUI |
| [11 — Web GUI](docs/phases/11-web-gui.md) | React SPA: Settings, Search, Logs, Knowledge Graph |

## Embedding models

| Adapter | Config `type` | Cost | Notes |
|---|---|---|---|
| Ollama (default) | `ollama` | Free | Offline, any model via `ollama pull` |
| OpenAI | `openai` | ~$0.02/1M tokens | `text-embedding-3-small` or `large`; Batch API option |

## Architecture

smart-search follows the **Plugin/Adapter Pattern** — adding a new data source or embedding model requires only a new file, no core changes.

```
Source Adapter  →  SyncEngine  →  Embedding Adapter  →  Vector Store
 CSV/JSON/MySQL/     hash diff,     Ollama / OpenAI     Qdrant / Weaviate
   REST API          incremental
```

See [docs/architecture.md](docs/architecture.md) for the full system diagram.

## Running Tests

```bash
cd sync-service
pip install -r requirements.txt pytest
pytest
```

## License

MIT
