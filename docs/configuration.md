# Configuration Reference

All configuration lives in a single `config.yaml` file per collection, placed under `configuration/<CollectionName>/config.yaml` (or `configuration/config.yaml` for a single-collection setup).

## Full example

```yaml
source:
  type: csv
  file_path: ./data/products.csv
  id_field: id
  delimiter: ","

embedding:
  type: ollama
  model: qwen3-embedding:4b
  endpoint: http://host.docker.internal:11434

weaviate:
  collection: Products
  text_fields:
    - name
    - description
  metadata_fields:
    - category
    - price
    - in_stock

sync:
  mode: incremental
  hash_fields: [id, name, description]
  schedule: manual

api:
  output_fields: [name, description, category, price]
  default_limit: 10
  max_limit: 100
```

---

## `source` block

### CSV source

```yaml
source:
  type: csv
  file_path: ./data/myfile.csv   # relative to project root
  id_field: id                   # column used as unique record ID
  delimiter: ","                 # column separator (use ";" for semicolon CSVs)
```

> **Column names**: spaces are automatically normalized to underscores (`Job Title` → `Job_Title`). Use underscores everywhere in `weaviate`, `sync`, and `api` sections.

### JSON source

```yaml
source:
  type: json
  file_path: ./data/items.json   # local file OR remote URL
  id_field: id
  json_key: results              # optional: key whose value is the records array
```

### REST API source

```yaml
source:
  type: rest_api
  url: https://api.example.com/v1/items
  id_field: id
  json_key: data

  # Authentication (optional)
  auth:
    type: bearer                 # none | bearer | api_key_header | api_key_param | basic
    token: ${MY_API_TOKEN}       # resolved from .env at runtime

  # Static query parameters (optional)
  params:
    language: en-US
    status: active

  # Pagination (optional)
  pagination:
    type: page                   # none | offset | page | cursor
    page_param: page
    total_pages_key: total_pages
    start_page: 1
    max_pages: 1000              # safety cap (default: 10000)
```

#### Auth strategies

| `type` | Required fields | Notes |
|---|---|---|
| `none` | — | No authentication |
| `bearer` | `token` | `Authorization: Bearer <token>` |
| `api_key_header` | `header_name`, `key` | Custom header |
| `api_key_param` | `param_name`, `key` | Query string parameter |
| `basic` | `username`, `password` | HTTP Basic Auth |

#### Pagination strategies

| `type` | Description |
|---|---|
| `none` | Single request, no pagination |
| `offset` | `?offset=0&limit=100` style |
| `page` | `?page=1` style with `total_pages` in response |
| `cursor` | Follows a `next` URL in the response body |

> **Env var substitution**: `${VAR_NAME}` in any auth/pagination field is resolved from `.env` at runtime. The GUI never stores or transmits the actual secret value.

---

## `embedding` block

```yaml
embedding:
  type: ollama                                  # ollama (default)
  model: qwen3-embedding:4b                     # any model pulled in Ollama
  endpoint: http://host.docker.internal:11434   # Ollama server URL
```

> Changing `model` triggers an automatic full re-index on next startup.

---

## `weaviate` block

```yaml
weaviate:
  collection: MyCollection    # PascalCase recommended
  text_fields:                # fields embedded for semantic search
    - description
    - title
  metadata_fields:            # fields stored but NOT embedded (filterable)
    - category
    - status
    - price
```

**Rules:**
- `text_fields` drive search quality — include descriptive, natural-language fields
- `metadata_fields` can be used in `?filter=Field:Value` queries
- Weaviate forbids `id` and `vector` as field names — they are automatically skipped
- Weaviate lowercases the first letter of every property (`Status` → `status`) — use lowercase in filter queries

---

## `sync` block

```yaml
sync:
  mode: incremental           # full | incremental
  hash_fields: [id, name]     # fields used for change detection
  schedule: manual            # manual | cron expression (e.g. "0 */6 * * *")
```

- `full`: drops and recreates the collection on every sync
- `incremental`: computes MD5 hash over `hash_fields` and upserts only changed records

---

## `api` block

```yaml
api:
  output_fields: [name, description, category]  # fields returned in /search response
  default_limit: 10                              # default result count
  max_limit: 100                                 # maximum allowed ?limit= value
```

---

## Multi-collection setup

Create one directory per collection under `configuration/`:

```
configuration/
  Products/
    config.yaml
  Articles/
    config.yaml
```

The web GUI and `/collections` endpoint enumerate all sub-directories automatically.
