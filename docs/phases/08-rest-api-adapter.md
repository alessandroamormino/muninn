# Phase 08 — REST API Adapter

Adds `RestAPIAdapter` — a generic HTTP source adapter that can connect to any REST API without writing code.

## What was built

### RestAPIAdapter

`sources/rest_api_adapter.py` implements `BaseSourceAdapter` for HTTP APIs. Configured entirely via `config.yaml`:

```yaml
source:
  type: rest_api
  url: https://api.example.com/v1/products
  id_field: id
  json_key: results            # optional: key containing the records array

  auth:
    type: bearer
    token: ${MY_API_TOKEN}     # resolved from .env

  params:
    status: active             # static query parameters

  pagination:
    type: page
    page_param: page
    total_pages_key: total_pages
    max_pages: 500
```

### Authentication strategies

| Strategy | Config | Description |
|---|---|---|
| `none` | — | No authentication |
| `bearer` | `token` | `Authorization: Bearer <token>` |
| `api_key_header` | `header_name` + `key` | Custom header (e.g., `X-API-Key`) |
| `api_key_param` | `param_name` + `key` | Query string key (e.g., `?api_key=...`) |
| `basic` | `username` + `password` | HTTP Basic Auth |

All credential fields support `${VAR}` substitution from `.env`.

### Pagination strategies

| Strategy | Description |
|---|---|
| `none` | Single request, no pagination |
| `offset` | `?offset=0&limit=100` incremented per page |
| `page` | `?page=1` with `total_pages` in response |
| `cursor` | Follows a `next` URL in the response body |

**Cursor pagination note**: the `next` URL returned by the API already contains all parameters. The adapter follows it verbatim without re-applying `params:` — this is the correct behavior for APIs like PokéAPI.

### Safety features

- `max_pages` (default: 10000) prevents infinite loops if the API returns incorrect pagination metadata
- `_filter_valid()` checks `val is None or val == ""` — not truthiness — so records with numeric ID `0` are not incorrectly dropped
- `JSONDecodeError` is wrapped in `AdapterError` with the raw response text for debugging

### Example configurations

**PokéAPI** (no auth, cursor pagination):
```yaml
source:
  type: rest_api
  url: https://pokeapi.co/api/v2/pokemon
  id_field: name
  json_key: results
  params:
    limit: 100
  pagination:
    type: cursor
    next_key: next
```

**TMDB** (bearer auth, page pagination):
```yaml
source:
  type: rest_api
  url: https://api.themoviedb.org/3/movie/popular
  id_field: id
  json_key: results
  auth:
    type: bearer
    token: ${TMDB_BEARER_TOKEN}
  params:
    language: en-US
  pagination:
    type: page
    page_param: page
    total_pages_key: total_pages
    start_page: 1
    max_pages: 500
```

## Key files

| File | Purpose |
|---|---|
| `sync-service/sources/rest_api_adapter.py` | `RestAPIAdapter` with auth and pagination |
