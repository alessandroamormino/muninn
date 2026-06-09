# User Guide

smart-search is a self-hosted semantic search engine. You point it at a data source (CSV, JSON, REST API, MySQL database), it indexes the data using AI embeddings, and gives you a search endpoint that understands natural language — not just exact keyword matches.

---

## What problems it solves

**Traditional keyword search** only finds records where the exact words appear. If you search for "senior engineer", you won't find "lead developer" even if they mean the same thing in your data.

**smart-search** understands meaning. It combines:
- **Semantic search** — finds records that are conceptually similar to your query
- **Keyword search (BM25)** — finds exact matches for names, codes, and identifiers

Both run together on every query, giving you the best of both worlds.

---

## Use cases

| Data | What you can search for |
|---|---|
| Employee directory | "Who leads the frontend team?", "product managers in Milan" |
| Product catalog | "waterproof hiking boots under 100€", "something like a Swiss Army knife" |
| Movie/media database | "80s sci-fi with a female protagonist" |
| Knowledge base / FAQ | "how do I reset my password?" |
| Any REST API | Index any paginated API endpoint and make it searchable |

---

## Web interface

Open [http://localhost:3000](http://localhost:3000) after starting the stack.

### Search page

The main search interface. Type a natural language query and get ranked results.

**Controls:**
- **Search bar** — type your query and press Enter
- **Search mode** — choose how search works:
  - `Hybrid` (default) — combines semantic + keyword, best for most queries
  - `Vector` — pure semantic, best for conceptual/language queries
  - `BM25` — pure keyword, best for exact codes or names
  - `FTS` — full-text search (Qdrant only)
- **Filters** — narrow results by a metadata field value (e.g. `Department: Engineering`)
- **Min score** — hide results below a relevance threshold (0.0–1.0)

Results show a `_score` field: higher is more relevant.

**Negation:** prefix a term with `-` to exclude results containing it.
Example: `engineers -junior` returns engineers but excludes junior ones.

### Settings page

Manage data sources and collections.

- **Entity list** (left sidebar) — all configured collections
- **YAML editor** — view and edit `config.yaml` directly in the browser
- **Upload wizard** — drag-drop a CSV or JSON file to create a new collection
- **Suggest config** — paste a CSV and let the AI suggest which fields to use for search vs metadata
- **MySQL wizard** — configure a MySQL database as a source through a step-by-step form
- **REST API form** — configure any HTTP API as a source
- **Sync tab** — trigger full re-index or incremental sync, see last sync status
- **Logs tab** — sync history for the selected collection

### Logs page

Full sync history across all collections: start time, duration, records synced, status (success / error).

### Graph page

A visual exploration of your data's embedding space. The system:
1. Fetches all vectors from the collection
2. Reduces dimensions with UMAP
3. Clusters similar records with HDBSCAN
4. Labels each cluster automatically using a local LLM

Use it to discover patterns in your data — which records are similar, which form natural groups, which are outliers.

---

## How sync works

smart-search keeps your data in sync without re-indexing everything every time.

**Full sync** (`POST /sync/full`): drops the collection and re-indexes all records from scratch. Use this when you change `text_fields`, switch embedding models, or want a clean slate.

**Incremental sync** (`POST /sync`): reads all records from the source, computes a hash of each record's key fields, and only re-embeds records that changed since the last sync. Unchanged records are skipped.

**Scheduled sync**: set a cron expression in `config.yaml` (e.g. `"0 */6 * * *"` for every 6 hours) and the system syncs automatically.

---

## Search API (for developers)

If you want to integrate smart-search into your own application:

```bash
GET http://localhost:8000/search?q=senior+engineer&collection=Employees&limit=10
```

Full API reference: [api-reference.md](api-reference.md)

---

## Multi-collection setup

You can have multiple independent collections (e.g. employees + products + articles) by creating a separate subfolder under `configuration/` for each one, each with its own `config.yaml`. The web GUI lists all collections in the sidebar and lets you switch between them.

---

## Supported data sources

| Source | Config `type` | Notes |
|---|---|---|
| CSV file | `csv` | Any delimiter, column names auto-normalized |
| JSON file or URL | `json` | Local file or remote HTTP URL |
| REST API | `rest_api` | 5 auth methods, 4 pagination types |
| MySQL / MariaDB | `mysql` | Flat queries + aggregated joins, SSL support |

See [configuration.md](configuration.md) for full config examples for each source type.
