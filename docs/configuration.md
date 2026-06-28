# Configuration Reference

Every collection has its own `config.yaml` under `configuration/<CollectionName>/config.yaml`.  
For a single-collection setup you can also place it at `configuration/config.yaml`.

```
configuration/
  Products/
    config.yaml      ← one collection
  Employees/
    config.yaml      ← another collection
  config.yaml        ← or a single default collection
```

---

## Quick orientation

A `config.yaml` has six top-level sections:

| Section | What it controls |
|---|---|
| `source` | Where data comes from (CSV, JSON, REST API, MySQL) |
| `embedding` | How text is turned into vectors (Ollama, OpenAI, …) |
| `vector_store` | Which fields are searchable, search mode, RAM/disk trade-offs |
| `sync` | Full vs incremental, change detection, scheduling |
| `api` | Which fields `/search` returns, pagination limits, cache |
| `graph` | Which fields appear as filters in the knowledge graph view |

---

## `source` block

### CSV

```yaml
source:
  type: csv
  file_path: ./data/employees.csv   # path relative to project root
  id_field: id                      # column used as unique record identifier
  delimiter: ","                    # "," (default) or ";" for European CSVs
```

**Column names**: spaces are automatically normalized to underscores on read.  
`Job Title` in the file becomes `Job_Title` everywhere else in the config.

---

### JSON

```yaml
source:
  type: json
  file_path: ./data/items.json   # local path OR remote HTTP/HTTPS URL
  id_field: id
  json_key: results              # optional: key whose value is the records array
                                 # omit if the root of the JSON is already an array
```

---

### REST API

```yaml
source:
  type: rest_api
  url: https://api.example.com/v1/items
  id_field: id
  json_key: data                 # key in the response body containing the records array

  # Authentication (optional)
  auth:
    type: bearer                 # see table below
    token: ${MY_API_TOKEN}       # ${VAR} is resolved from .env at runtime

  # Static query parameters added to every request (optional)
  params:
    language: en-US
    status: active

  # Pagination (optional — omit for single-page APIs)
  pagination:
    type: page                   # see table below
    page_param: page
    total_pages_key: total_pages
    start_page: 1
    max_pages: 1000              # safety cap against infinite loops (default: 10000)
```

#### Auth strategies

| `type` | Required fields | How it works |
|---|---|---|
| `none` | — | No authentication header |
| `bearer` | `token` | `Authorization: Bearer <token>` |
| `api_key_header` | `header_name`, `key` | Custom header, e.g. `X-Api-Key: <key>` |
| `api_key_param` | `param_name`, `key` | Added as query string parameter |
| `basic` | `username`, `password` | HTTP Basic Auth |

All secret values accept `${VAR}` substitution from `.env`.  
The GUI never stores or transmits the actual secret — only the variable name.

#### Pagination strategies

| `type` | Description | Key parameters |
|---|---|---|
| `none` | Single request, all records in one response | — |
| `page` | `?page=1`, `?page=2`, … | `page_param`, `total_pages_key`, `start_page` |
| `offset` | `?offset=0&limit=100`, `?offset=100&limit=100`, … | `offset_param`, `limit_param`, `page_size` |
| `cursor` | Follows a `next` URL in the response body | `next_key` (default: `"next"`) |

> **Cursor note**: the `next` URL already contains all parameters. Static `params` are NOT re-appended on subsequent cursor hops.

#### Full REST API example — TMDB

```yaml
source:
  type: rest_api
  url: https://api.themoviedb.org/3/movie/popular
  id_field: id
  json_key: results
  auth:
    type: bearer
    token: ${TMDB_API_KEY}
  params:
    language: en-US
  pagination:
    type: page
    page_param: page
    total_pages_key: total_pages
    start_page: 1
    max_pages: 500
```

---

### MySQL / MariaDB

MySQL configuration lives in a nested `mysql:` sub-block under `source:`.  
**Credentials must always use `${VAR}` substitution** — never put passwords in plain text.

#### Minimal example (single table, no joins)

```yaml
source:
  type: mysql
  mysql:
    host: ${MYSQL_HOST}         # e.g. localhost or 192.168.1.10
    port: 3306
    database: ${MYSQL_DB}
    user: ${MYSQL_USER}
    password: ${MYSQL_PASSWORD}

    query:
      from: products            # table name (backtick-escaped internally)
      fields:
        - id
        - name
        - description
        - category
        - price
      id_field: id              # column used as unique record identifier
      hash_fields: [id, name, description, price]   # fields used for change detection
```

Then in `.env`:

```bash
MYSQL_HOST=localhost
MYSQL_DB=mydb
MYSQL_USER=myuser
MYSQL_PASSWORD=mypassword
```

#### `query` sub-block — all options

| Field | Type | Default | Description |
|---|---|---|---|
| `from` | string | required | Source table name |
| `fields` | list[str] | required | Columns to fetch (must include `id_field`) |
| `id_field` | string | `"id"` | Column used as the unique record identifier |
| `hash_fields` | list[str] | `[]` | Columns whose concatenated MD5 hash detects changes |
| `joins` | list | `[]` | Additional tables to join (see below) |
| `fetch_chunk_size` | int | `10000` | Rows per SELECT … LIMIT page (lower = less RAM per batch) |

> **`hash_fields` tip**: include every column you care about detecting changes in.  
> The system computes `MD5(str(val1) + str(val2) + …)` and skips records whose hash hasn't changed since the last sync.

#### Joins

Each item in `joins:` adds data from a related table to every record.

```yaml
query:
  from: employees
  fields: [id, name, role, bio]
  id_field: id
  hash_fields: [id, name, role, bio]

  joins:
    # many-to-one join (flat): adds columns from the joined table as direct fields
    - table: departments
      "on": "employees.department_id = departments.id"
      fields: [department_name, location]
      aggregate: false          # default

    # one-to-many join (aggregated): concatenates child rows into a single string field
    - table: skills
      "on": "employees.id = skills.employee_id"
      fields: [skill_name, level]
      aggregate: true
      separator: ", "           # how to join multiple values (default: ", ")
      "as": employee_skills     # name of the resulting field (default: first field name)
```

**Join modes:**

| `aggregate` | Behavior | Use case |
|---|---|---|
| `false` (default) | LEFT JOIN — adds columns as flat fields on the record | Many-to-one: department, category, status |
| `true` | Separate query + Python groupby — concatenates rows into a string | One-to-many: tags, skills, order lines |

> **YAML pitfall**: PyYAML parses bare `on:` and `as:` as booleans. Always quote them: `"on":` and `"as":`.

#### SSL (optional)

```yaml
mysql:
  host: ${MYSQL_HOST}
  database: ${MYSQL_DB}
  user: ${MYSQL_USER}
  password: ${MYSQL_PASSWORD}
  ssl_ca: ${MYSQL_SSL_CA}       # path to CA certificate
  ssl_cert: ${MYSQL_SSL_CERT}   # path to client certificate
  ssl_key: ${MYSQL_SSL_KEY}     # path to client private key
  query:
    from: ...
```

#### Full MySQL example with joins

```yaml
source:
  type: mysql
  mysql:
    host: ${MYSQL_HOST}
    port: 3306
    database: ${MYSQL_DB}
    user: ${MYSQL_USER}
    password: ${MYSQL_PASSWORD}

    query:
      from: employees
      fields:
        - id
        - full_name
        - job_title
        - bio
        - department_id
      id_field: id
      hash_fields: [id, full_name, job_title, bio]
      fetch_chunk_size: 5000

      joins:
        - table: departments
          "on": "employees.department_id = departments.id"
          fields: [name, city]
          aggregate: false
          "as": department

        - table: employee_skills
          "on": "employees.id = employee_skills.employee_id"
          fields: [skill_name]
          aggregate: true
          separator: ", "
          "as": skills

embedding:
  type: ollama
  model: qwen3-embedding:4b
  endpoint: http://host.docker.internal:11434

vector_store:
  collection: Employees
  search_mode: hybrid
  text_fields:
    - bio
    - job_title
    - skills
  metadata_fields:
    - full_name
    - department
    - city

sync:
  mode: incremental
  hash_fields: [id, full_name, job_title, bio]
  schedule: manual

api:
  output_fields: [full_name, job_title, department, city, skills]
  default_limit: 10
  max_limit: 50
```

---

## `embedding` block

### Ollama (offline, default)

Best for: privacy, no API costs, Mac with Metal GPU, datasets up to ~500K records.

```yaml
embedding:
  type: ollama
  model: qwen3-embedding:4b                     # any model pulled via `ollama pull`
  endpoint: http://host.docker.internal:11434   # Ollama server URL (host.docker.internal on Mac)
```

**Available models (examples):**

| Model | Dimensions | Notes |
|---|---|---|
| `qwen3-embedding:4b` | 2560 | Multilingual, default recommendation |
| `nomic-embed-text` | 768 | Lighter, English-focused |
| `bge-m3` | 1024 | Strong multilingual, higher quality |

> Changing `model` triggers an automatic full re-index on next startup (detected via `model_version.json`).

---

### OpenAI (cloud, high throughput)

Best for: large datasets (>500K records), highest quality embeddings, when offline is not a constraint.  
Requires `OPENAI_API_KEY` in `.env`.

```yaml
embedding:
  type: openai
  model: text-embedding-3-small    # text-embedding-3-small (default) or text-embedding-3-large
  api_key: ${OPENAI_API_KEY}       # resolved from .env
  openai_batch: false              # false = sync path (default); true = Batch API (see below)
  max_retries: 10                  # retries on HTTP 429 rate-limit errors (default: 10)
```

**Available models:**

| Model | Dimensions | Cost | Notes |
|---|---|---|---|
| `text-embedding-3-small` | 1536 | $0.02 / 1M tokens | Recommended — good quality/cost balance |
| `text-embedding-3-large` | 3072 | $0.13 / 1M tokens | Highest quality, ~6× more expensive |

**Batch API** (`openai_batch: true`):  
Uses OpenAI's asynchronous Batch API — 50% cost reduction but up to 24h processing time.  
Only useful for one-off bulk loads ≤ 50K lines per batch file. For 1M+ records, use the sync path (`false`) with the streaming pipeline instead.

```yaml
embedding:
  type: openai
  model: text-embedding-3-small
  api_key: ${OPENAI_API_KEY}
  openai_batch: true     # submit job → poll → collect; checkpoint-safe across restarts
  max_retries: 10
```

**Rate limiting**: the adapter automatically retries on HTTP 429 with exponential backoff + jitter (max delay: 120s). No manual tuning needed.  
**Key masking**: `OPENAI_API_KEY` is never logged — appears as `sk-...****` in all output.

---

## `vector_store` block

This section defines how your data is stored and searched in the vector database.

### Common fields

```yaml
vector_store:
  collection: MyCollection      # name in the vector DB (PascalCase recommended)
  search_mode: hybrid           # hybrid | vector | bm25 | fts
  text_fields:
    - description               # fields embedded for semantic search
    - title
  metadata_fields:
    - category                  # fields stored but NOT embedded (filterable via ?filter=)
    - price
    - status
```

**`search_mode`:**

| Mode | What it does | Best for | Needs Ollama? |
|---|---|---|---|
| `hybrid` (default) | BM25 keyword + dense vector, fused with RRF | Most use cases — finds names, codes AND concepts | yes |
| `vector` | Pure semantic dense kNN | Conceptual queries, language-independent, m/f & synonyms | yes |
| `bm25` | True BM25 ranking (IDF/TF) over the sparse index | Exact codes, SKUs, identifiers — ranked by relevance | no |
| `fts` | Boolean full-text: term presence + Snowball stemming + fuzzy + exact-phrase bonus | Recall-first coverage and exact-phrase matching (Qdrant only) | no |

> Full deep-dive — what each mode covers (typos, plural, gender, synonyms, phonetics), how to add custom dictionaries, and worked examples — lives in **[search.md](search.md)**.

**`text_fields`** accept per-field boost weights (optional):

```yaml
text_fields:
  description: 1.0    # standard weight
  title: 2.0          # title matches count double in BM25 scoring
  tags: 0.5           # lower weight for tag field
```

Plain list format (all weights = 1.0) is equivalent and simpler when you don't need custom weights:

```yaml
text_fields:
  - description
  - title
  - tags
```

**Rules:**
- `text_fields` drive search quality — include descriptive, natural-language fields
- `metadata_fields` can be used in `?filter=Field:Value` queries (exact match)
- Weaviate: forbids `id` and `vector` as field names — automatically skipped
- Weaviate: lowercases the first letter of every property (`Status` → `status`) — use lowercase in filter queries

---

### Qdrant-specific options (`qdrant_opts`)

These options control RAM usage and search performance for the Qdrant vector store.  
All are optional — omitting them uses safe defaults.

```yaml
vector_store:
  collection: Products
  search_mode: hybrid
  text_fields: [description, tags]
  metadata_fields: [category, price]

  qdrant_opts:
    on_disk: false              # true = store raw vectors on disk (memmap); false = keep in RAM
    quantization:
      type: none                # none | sq | bq (see table below)
      quantile: 0.99            # SQ only — upper quantile for calibration (default: 0.99)
      always_ram: true          # keep quantized vectors in RAM even with on_disk=true (recommended)
    search:
      rescore: false            # true = re-rank results with full-precision vectors after ANN
      oversampling: 2.0         # fetch 2x candidates before rescoring (default: 2.0)
```

#### `on_disk` — when to use it

| Dataset size | `on_disk` | Reason |
|---|---|---|
| < 100K records | `false` (default) | RAM is fast; disk access adds latency |
| 100K–1M records | `true` recommended | Avoids OOM; memmap is fast with SSD |
| > 1M records | `true` required | 1M × 1536-dim floats = ~6 GB RAM without it |

> **Important**: `on_disk` cannot be changed on an existing collection. Set it before the first full sync. Changing it requires a full re-index.

Example for a 1M-record product catalog:

```yaml
qdrant_opts:
  on_disk: true
  quantization:
    type: sq
    always_ram: true
  search:
    rescore: true
    oversampling: 2.0
```

#### `quantization.type` — RAM vs quality trade-off

| Type | RAM reduction | Quality loss | When to use |
|---|---|---|---|
| `none` (default) | — | — | < 50K records, quality is critical |
| `sq` (Scalar, int8) | ~4× | ~1–2% | 50K–500K records — good balance |
| `bq` (Binary) | ~32× | ~5–10% | RAM is the hard constraint |

> **Important**: quantization cannot be changed on an existing collection. Set it before the first full sync.

#### `search.rescore`

When `quantization.type` is `sq` or `bq`, enabling `rescore: true` fetches `oversampling × limit` candidates from the quantized index and re-ranks them using full-precision vectors. Recovers most of the quality loss from quantization.

```yaml
qdrant_opts:
  quantization:
    type: sq
  search:
    rescore: true
    oversampling: 2.0   # fetch 2× limit candidates before re-ranking
```

#### `fts` — full-text search settings (Qdrant only, `search_mode: fts`)

```yaml
vector_store:
  search_mode: fts
  fts:
    language: it          # stemmer language: en, it, de, fr, es, pt, nl, ru, sv, fi, da, …
    match_mode: and       # and (default) | or — how multiple query terms are combined
    use_omw: false        # true = download Open Multilingual Wordnet synonyms at sync time
```

---

### Weaviate-specific options

For Weaviate, quantization and HNSW tuning are configured at the top level of `vector_store`:

```yaml
vector_store:
  collection: Products
  text_fields: [description, name]
  metadata_fields: [category, price]

  # Quantization (Weaviate only — set before first sync, cannot change on existing collection)
  quantization: none   # none (default) | pq | bq | sq

  # HNSW index tuning (optional)
  hnsw:
    ef: 128              # search quality: higher = better recall, slower query (default: 64)
    max_connections: 32  # graph connectivity (default: 64) — IMMUTABLE after collection creation
```

> A warning appears in `GET /sync/status` when `total_records > 50,000` and `quantization: none`.

---

### Quantization — full comparison

Quantization compresses vectors to reduce RAM. The available types and where they are configured differ between the two vector stores.

> **Key rule**: `vector_store.quantization` (top-level) is **Weaviate only** and is ignored by Qdrant.  
> Qdrant quantization goes under `vector_store.qdrant_opts.quantization.type`.

| Type | Weaviate config | Qdrant config | RAM reduction | Quality loss | When to use |
|---|---|---|---|---|---|
| None | `quantization: none` | `qdrant_opts.quantization.type: none` | — | — | < 50K records, or quality is critical |
| SQ (Scalar, int8) | `quantization: sq` | `qdrant_opts.quantization.type: sq` | ~4× | ~1–2% | 50K–500K records — best balance |
| PQ (Product) | `quantization: pq` | **not supported** | ~32× | ~2–5% | > 100K records, Weaviate only |
| BQ (Binary) | `quantization: bq` | `qdrant_opts.quantization.type: bq` | ~32–128× | ~10–15% | RAM is the hard constraint |

> Quantization cannot be changed on an existing collection. Set it before the first full sync.  
> When using Qdrant + quantization, always enable `search.rescore: true` to recover recall quality.

---

---

## Vector store selection

> **The vector store is a project-level setting, not per-entity.**  
> All collections within a project share the same vector database. You cannot use Qdrant for one collection and Weaviate for another.

### Where to configure it

In your `.env` file, set three variables together:

```bash
# Qdrant (recommended default)
VECTOR_STORE_ENGINE=qdrant
COMPOSE_PROFILES=qdrant
VECTOR_STORE_URL=http://vector-db-qdrant:6333

# — or — Weaviate (legacy, original POC default)
# VECTOR_STORE_ENGINE=weaviate
# COMPOSE_PROFILES=weaviate
# VECTOR_STORE_URL=http://vector-db:8080
```

`COMPOSE_PROFILES` is read automatically by Docker Compose from `.env` — you do not need to pass `--profile` on the command line when it is set there.

### Starting the stack

```bash
# With Qdrant (default — COMPOSE_PROFILES=qdrant already in .env)
docker compose up

# Explicitly passing the profile (overrides .env)
docker compose --profile qdrant up
docker compose --profile weaviate up

# Rebuild images and start
docker compose --profile qdrant up --build
```

After changing the vector store engine you must:
1. Update the three `.env` variables
2. Run a full re-index (`POST /sync/full`) — data does not transfer between vector stores automatically

### Qdrant vs Weaviate — quick comparison

| Feature | Qdrant | Weaviate |
|---|---|---|
| **Language** | Rust | Go |
| **RAM optimization** | `on_disk` memmap + SQ/BQ | SQ/PQ/BQ quantization |
| **Product Quantization (PQ)** | ❌ not supported | ✅ |
| **Qdrant dashboard** | ✅ built-in at `:6333/dashboard` | ❌ |
| **gRPC support** | ✅ (preferred) | ✅ |
| **Search modes** | hybrid, vector, bm25, fts | hybrid, vector, bm25 |
| **Docker port** | 6333 (REST), 6334 (gRPC) | 8080 (REST), 50051 (gRPC) |
| **Best for** | Large datasets, RAM-constrained setups | Feature-rich schema, PQ at scale |

> Qdrant is the current recommended default. Weaviate is still fully supported.

---

## `sync` block

```yaml
sync:
  mode: incremental           # full | incremental
  hash_fields: [id, name]     # columns whose MD5 hash detects record changes
  schedule: manual            # manual | cron expression
```

| Field | Options | Notes |
|---|---|---|
| `mode` | `full` | Drop and recreate the collection on every sync |
| `mode` | `incremental` | Hash-compare each record; upsert only changed ones |
| `schedule` | `manual` | Sync only when you call `POST /sync` or `POST /sync/full` |
| `schedule` | cron string | e.g. `"0 */6 * * *"` = every 6 hours |

**When to use `full` vs `incremental`:**
- Use `full` when you switch `text_fields` or change the embedding model
- Use `incremental` for routine data refreshes when the schema is stable
- After switching models, a `full` re-index is triggered automatically on startup

---

## `api` block

```yaml
api:
  output_fields: [name, description, category, price]   # fields returned in /search response
  default_limit: 10                                      # default result count when ?limit= is absent
  max_limit: 100                                         # maximum allowed ?limit= value (null = no cap)

  # Search result cache
  cache_mode: exact              # exact | normalized | semantic
  cache_ttl_seconds: 300         # cache entry lifetime in seconds (default: 5 minutes)
  semantic_cache_threshold: 0.90 # semantic mode only — similarity threshold for a cache hit
```

**`max_limit`**: set to `null` (or omit) to allow `?limit=` to fetch all records in one call.  
Useful for export/reporting use cases but can be slow on large collections.

**Cache modes:**

| Mode | Behavior | When to use |
|---|---|---|
| `exact` (default) | Only identical queries share a cache hit | Most cases |
| `normalized` | Strips punctuation, lowercases before lookup | Noisy user input |
| `semantic` | Embeds the query and considers it a hit if similarity ≥ threshold | When wording varies but intent is the same |

---

## `graph` block (optional)

Controls which fields appear as filter options in the knowledge graph visualization.

```yaml
graph:
  filter_fields:
    - department
    - location
    - status
```

If omitted, no filter controls appear in the graph view.

---

## Complete examples

### Example 1 — CSV, offline embeddings, Weaviate

```yaml
source:
  type: csv
  file_path: ./data/employees.csv
  id_field: id
  delimiter: ","

embedding:
  type: ollama
  model: qwen3-embedding:4b
  endpoint: http://host.docker.internal:11434

vector_store:
  collection: Employees
  search_mode: hybrid
  text_fields:
    - bio
    - job_title
  metadata_fields:
    - department
    - location

sync:
  mode: incremental
  hash_fields: [id, bio, job_title]
  schedule: manual

api:
  output_fields: [id, name, job_title, department, location]
  default_limit: 10
  max_limit: 100
```

---

### Example 2 — MySQL with joins, OpenAI embeddings, Qdrant + on_disk

Large product catalog (~1M records) on MySQL, OpenAI embeddings, Qdrant with RAM optimization:

```yaml
source:
  type: mysql
  mysql:
    host: ${MYSQL_HOST}
    port: 3306
    database: ${MYSQL_DB}
    user: ${MYSQL_USER}
    password: ${MYSQL_PASSWORD}

    query:
      from: products
      fields: [id, sku, name, description, unit]
      id_field: id
      hash_fields: [id, name, description, unit]
      fetch_chunk_size: 10000

      joins:
        - table: product_categories
          "on": "products.category_id = product_categories.id"
          fields: [category_name]
          aggregate: false

        - table: product_tags
          "on": "products.id = product_tags.product_id"
          fields: [tag]
          aggregate: true
          separator: ", "
          "as": tags

embedding:
  type: openai
  model: text-embedding-3-small
  api_key: ${OPENAI_API_KEY}
  openai_batch: false
  max_retries: 10

vector_store:
  collection: Products
  search_mode: hybrid
  text_fields:
    - description
    - name
    - tags
  metadata_fields:
    - sku
    - category_name
    - unit
  qdrant_opts:
    on_disk: true
    quantization:
      type: sq
      always_ram: true
    search:
      rescore: true
      oversampling: 2.0

sync:
  mode: full
  hash_fields: [id]
  schedule: manual

api:
  output_fields: [name, description, sku, category_name, tags, unit]
  default_limit: 10
  max_limit: 50
```

---

### Example 3 — REST API, bearer auth, cursor pagination

```yaml
source:
  type: rest_api
  url: https://api.example.com/v2/articles
  id_field: uuid
  json_key: items
  auth:
    type: bearer
    token: ${ARTICLES_API_TOKEN}
  params:
    per_page: 200
  pagination:
    type: cursor
    next_key: next_page_url

embedding:
  type: ollama
  model: qwen3-embedding:4b
  endpoint: http://host.docker.internal:11434

vector_store:
  collection: Articles
  search_mode: hybrid
  text_fields: [title, body, summary]
  metadata_fields: [author, published_at, category]

sync:
  mode: incremental
  hash_fields: [uuid, title, body]
  schedule: "0 */4 * * *"   # sync every 4 hours

api:
  output_fields: [title, summary, author, published_at, category]
  default_limit: 10
  max_limit: 100
```

---

## Choosing the right configuration

### Which embedding adapter?

| Situation | Recommendation |
|---|---|
| Privacy-first, offline, Mac | `ollama` with `qwen3-embedding:4b` |
| Highest quality, budget available | `openai` with `text-embedding-3-small` |
| One-off bulk load, want 50% cost savings | `openai` with `openai_batch: true` |
| Large multilingual dataset | `ollama` with `bge-m3`, or `openai` |

### Which `search_mode`?

| Content type | Recommendation |
|---|---|
| Natural language descriptions, bios, articles | `hybrid` (default) |
| Product codes, SKUs, identifiers | `bm25` or `hybrid` |
| Conceptual queries across languages | `vector` |
| Exact phrase search with stemming | `fts` (Qdrant only) |

### When to set `on_disk: true`?

Rule of thumb: if `num_records × dimensions × 4 bytes` exceeds available RAM, set `on_disk: true`.

| Records | Dimensions | RAM needed | Recommendation |
|---|---|---|---|
| 50K | 1536 | ~300 MB | `on_disk: false` |
| 200K | 1536 | ~1.2 GB | Consider `on_disk: true` |
| 1M | 1536 | ~6 GB | `on_disk: true` required |
| 1M | 1024 | ~4 GB | `on_disk: true` recommended |
