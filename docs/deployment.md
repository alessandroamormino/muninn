# Deployment Guide

---

## Choosing a vector store

Before starting the stack, decide which vector store to use. Edit `.env` and set these three variables consistently:

```bash
# Qdrant (recommended default — already set in .env.example)
VECTOR_STORE_ENGINE=qdrant
COMPOSE_PROFILES=qdrant
VECTOR_STORE_URL=http://vector-db-qdrant:6333

# — or — Weaviate
# VECTOR_STORE_ENGINE=weaviate
# COMPOSE_PROFILES=weaviate
# VECTOR_STORE_URL=http://vector-db:8080
```

`COMPOSE_PROFILES` is picked up automatically from `.env` by Docker Compose — no need to pass `--profile` on the command line. See [configuration.md — Vector store selection](configuration.md#vector-store-selection) for the full comparison.

---

## Local / Development (macOS)

Ollama runs natively on macOS to use the Metal GPU (unified memory). The Docker stack runs the vector DB, the sync/search service, and the frontend.

```bash
# 1. Install and start Ollama
brew install ollama
ollama serve   # or use the Ollama desktop app

# 2. Pull models
ollama pull qwen3-embedding:4b   # embedding model (~2.5 GB)
ollama pull qwen2.5:3b           # LLM for auto-config and graph cluster naming

# 3. Configure
cp .env.example .env
# Edit .env — set the three VECTOR_STORE_* variables (default: qdrant, already set)
# Edit configuration/<YourCollection>/config.yaml  (see docs/configuration.md)

# 4. Start
docker compose up
```

Services after startup:
- Web GUI: http://localhost:3000
- API + Swagger: http://localhost:8000 / http://localhost:8000/docs
- Qdrant dashboard: http://localhost:6333/dashboard *(Qdrant only)*
- Weaviate console: http://localhost:8080 *(Weaviate only)*

---

## Production (Linux)

On Linux, run Ollama as a Docker container alongside the other services.

### docker-compose.override.yml

```yaml
services:
  embedder:
    image: ollama/ollama
    container_name: embedder
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]   # remove if no GPU available
    restart: unless-stopped

  orchestrator:
    environment:
      OLLAMA_ENDPOINT: http://embedder:11434

volumes:
  ollama_data:
    driver: local
```

```bash
# Pull models inside the container (one-time)
docker exec -it embedder ollama pull qwen3-embedding:4b
docker exec -it embedder ollama pull qwen2.5:3b

# Start full stack
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in as needed.

### Vector store (required — pick one set)

```bash
# Qdrant
VECTOR_STORE_ENGINE=qdrant
COMPOSE_PROFILES=qdrant
VECTOR_STORE_URL=http://vector-db-qdrant:6333

# Weaviate
# VECTOR_STORE_ENGINE=weaviate
# COMPOSE_PROFILES=weaviate
# VECTOR_STORE_URL=http://vector-db:8080
```

### Authentication (required in production)

```bash
# Generate a secure secret: python -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET=your_generated_secret_here
ADMIN_USERNAME=admin
ADMIN_PASSWORD=changeme   # change this
```

### MySQL source (optional — only if using MySQLAdapter)

```bash
MYSQL_HOST=localhost
MYSQL_DB=mydb
MYSQL_USER=myuser
MYSQL_PASSWORD=mypassword
```

### External embedding APIs (optional)

```bash
OPENAI_API_KEY=sk-...       # required for embedding.type: openai
COHERE_API_KEY=             # reserved for future CohereEmbeddingAdapter
VOYAGE_API_KEY=             # reserved for future VoyageEmbeddingAdapter
```

### External REST API secrets (optional — per-source, named however you like)

```bash
MY_API_TOKEN=your_token     # referenced in config.yaml as auth.token: ${MY_API_TOKEN}
TMDB_API_KEY=               # example: TMDB bearer token
```

> **Security**: `.env` is gitignored. Never commit credentials to version control.  
> The web GUI sends only the env var **name** (e.g. `MY_API_TOKEN`), never the value itself.

---

## Data directory

Place CSV/JSON source files under `data/` (gitignored):

```
data/
  products.csv
  articles.json
```

Reference them in `config.yaml` as `file_path: ./data/products.csv`.  
The `data/` directory is mounted read-only into the `orchestrator` container.

---

## Configuration directory

One sub-directory per collection:

```
configuration/
  Products/
    config.yaml
  Employees/
    config.yaml
```

The `configuration/` directory is mounted read-write into the `orchestrator` container. The web GUI can create and update config files via the Settings page without restarting the container.

---

## Persisted volumes

| Volume | Contents | Which engine |
|---|---|---|
| `qdrant_data` | Qdrant vector store | Qdrant |
| `weaviate_data` | Weaviate vector store | Weaviate |
| `sync_data` | Sync state hashes (incremental sync tracking) | Both |

To wipe the vector store and force a full re-index:

```bash
# Qdrant
docker compose --profile qdrant down -v
docker compose --profile qdrant up
curl -X POST "http://localhost:8000/sync/full?collection=MyCollection"

# Weaviate
docker compose --profile weaviate down -v
docker compose --profile weaviate up
curl -X POST "http://localhost:8000/sync/full?collection=MyCollection"
```

---

## Health checks

All services have health checks configured in `docker-compose.yml`.  
The frontend waits for the orchestrator; the orchestrator waits for whichever vector DB container is active.

```bash
# Check container health status
docker ps

# View logs
docker logs orchestrator --tail 50
docker logs vector-db-qdrant --tail 50   # Qdrant
docker logs vector-db --tail 50          # Weaviate
```

---

## Switching vector stores

Switching from one vector store to the other requires a full re-index — data does not transfer automatically.

```bash
# 1. Stop the stack
docker compose down

# 2. Update .env (change the three VECTOR_STORE_* variables)
nano .env

# 3. Restart with the new engine
docker compose up

# 4. Re-index all collections
curl -X POST "http://localhost:8000/sync/full?collection=MyCollection"
```

---

## Updating embedding models

1. Pull the new model: `ollama pull new-model:version`
2. Update `config.yaml`: set `embedding.model: new-model:version`
3. Restart the orchestrator: `docker compose restart orchestrator`
4. The system detects the model change automatically via `model_version.json` and triggers a full re-index.

---

## Running tests

Tests run on the host (not in Docker):

```bash
cd sync-service
pip install -r requirements.txt pytest
pytest
```

The test suite stubs out external dependencies (Qdrant, Weaviate, Ollama) — no live services are required.
