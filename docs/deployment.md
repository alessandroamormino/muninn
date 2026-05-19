# Deployment Guide

## Local / Development (macOS)

Ollama runs natively on macOS to use the Metal GPU (unified memory). The Docker stack consists of 2 containers.

```bash
# 1. Install and start Ollama
brew install ollama
ollama serve  # or use the Ollama desktop app

# 2. Pull models
ollama pull qwen3-embedding:4b    # embedding model (~2.5 GB)
ollama pull qwen2.5:3b            # LLM for auto-config and graph cluster naming

# 3. Configure
cp .env.example .env
# Edit configuration/config.yaml (see docs/configuration.md)

# 4. Start
docker-compose up
```

Services:
- Web GUI: http://localhost:3000
- API: http://localhost:8000
- Swagger: http://localhost:8000/docs
- Weaviate console: http://localhost:8080

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
              capabilities: [gpu]  # remove if no GPU
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
docker-compose -f docker-compose.yml -f docker-compose.override.yml up -d
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in as needed:

```bash
# Required
WEAVIATE_URL=http://vector-db:8080

# Optional — API keys for external sources (REST API adapter)
TMDB_BEARER_TOKEN=your_token_here
MY_API_KEY=your_key_here

# Optional — relational DB credentials (for future MySQL/PostgreSQL adapters)
DB_USER=
DB_PASSWORD=
DB_HOST=
DB_PORT=
DB_NAME=

# Optional — MongoDB
MONGODB_URI=
```

> **Security note**: `.env` is gitignored. Never commit credentials to version control. The web GUI sends only the env var name (e.g. `MY_API_KEY`), never the value.

---

## Data Directory

Place CSV/JSON source files under `data/` (gitignored):

```
data/
  products.csv
  articles.json
```

Reference them in `config.yaml` as `file_path: ./data/products.csv`.

The `data/` directory is mounted into the `orchestrator` container via `docker-compose.yml`.

---

## Configuration Directory

One sub-directory per collection:

```
configuration/
  Products/
    config.yaml
  Articles/
    config.yaml
```

The `configuration/` directory is mounted read-write into the `orchestrator` container. The web GUI can create and update config files via the Settings page.

---

## Persisted Volumes

| Volume | Contents |
|---|---|
| `weaviate_data` | Weaviate vector store |
| `sync_data` | Sync state hashes (incremental sync tracking) |

To reset the vector store and force a full re-index:

```bash
docker-compose down -v  # removes all volumes
docker-compose up
curl -X POST http://localhost:8000/sync/full?collection=MyCollection
```

---

## Health Checks

All three services have health checks configured in `docker-compose.yml`. The frontend waits for the orchestrator, which waits for Weaviate.

```bash
# Check container health
docker ps

# View logs
docker logs orchestrator --tail 50
docker logs vector-db --tail 50
```

---

## Updating Embedding Models

1. Pull the new model in Ollama: `ollama pull new-model:version`
2. Update `config.yaml`: set `embedding.model: new-model:version`
3. Restart the orchestrator: `docker-compose restart orchestrator`
4. The system detects the model change automatically and triggers a full re-index.

---

## Running Tests

Tests run on the host (not in Docker):

```bash
cd sync-service
pip install -r requirements.txt pytest
pytest
```

The test suite uses a Weaviate stub (`conftest.py`) — no live Weaviate or Ollama instance is required to run tests.
