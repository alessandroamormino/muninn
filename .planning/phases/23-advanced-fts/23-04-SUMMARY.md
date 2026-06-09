---
phase: 23-advanced-fts
plan: "04"
subsystem: api
tags:
  - api
  - search
  - suggest
  - autocomplete
  - fuzzy
  - omw
  - match_mode
  - field_weights
  - llm-prompt
  - rate-limit
dependency_graph:
  requires:
    - 23-02  # fuzzy.py + synonyms.py OMW helpers
    - 23-03  # qdrant_store._fuzzy_vocab populated at index time
  provides:
    - GET /search/suggest (Qdrant-only, JWT, rate-limited 60/min)
    - GET /search ?match_mode=and|or query param
    - fuzzy + OMW expansion in query pipeline
    - api/setup.py field_weights in LLM prompt + response
  affects:
    - 23-05  # frontend plan consumes GET /search/suggest and ?match_mode=
tech_stack:
  added: []
  patterns:
    - FastAPI route with explicit @limiter.limit() decorator
    - Query param with Literal type validation for AND/OR enum
    - Module-level dict read at query time (fuzzy vocab from Plan 03)
    - OMW expansion gated on use_omw config flag
key_files:
  created:
    - sync-service/tests/test_suggest.py
    - sync-service/tests/test_search_router_match_mode.py
    - sync-service/tests/test_setup_field_weights.py
  modified:
    - sync-service/api/search.py
    - sync-service/api/setup.py
    - sync-service/vector_stores/weaviate_store.py
decisions:
  - "Path-traversal guard (_COLLECTION_RE check) applied BEFORE engine guard in /search/suggest — ensures 422 even for non-Qdrant engines"
  - "limiter re-used from api.auth._limiter singleton (not a new instance) to keep rate-limit state shared"
  - "match_mode_override accepted by WeaviateVectorStore.search() as silently-ignored kwarg for API parity (Phase 23 note in docstring)"
  - "Test config YAML uses vector_store: block with search_mode: fts to avoid Ollama calls in unit tests"
  - "Worktree lacked configuration/ dir — created minimal copy from main repo to unblock api/ module imports in tests"
metrics:
  duration: "9 min"
  completed_date: "2026-06-09"
  tasks_completed: 3
  tasks_total: 4
  files_changed: 6
---

# Phase 23 Plan 04: API Layer — suggest, match_mode, fuzzy/OMW, field_weights Summary

**One-liner:** FastAPI layer wiring for Phase 23 query-time features: GET /search/suggest (Qdrant-only, JWT-protected, rate-limited at 60/min), ?match_mode=and|or param on GET /search, fuzzy + OMW expansion in query pipeline, and field_weights in setup.py LLM prompt + response.

## What Was Built

### Task 1: RED — Failing tests (commit 5244dc9)

Created three new test files covering the entire plan 04 contract:

- `sync-service/tests/test_suggest.py` — 9 tests for GET /search/suggest: Weaviate fallback to `[]`, path-traversal guard, missing config, deduplication, limit, first text_field used, unauthenticated 401, Qdrant error graceful, `@limiter.limit("60/minute")` decorator presence check
- `sync-service/tests/test_search_router_match_mode.py` — 8 tests: 4 for `?match_mode=` forwarding (and/or/none/invalid), 4 for fuzzy expansion wiring (single-term expansion, non-trivial variant check, 3+-term guard, Weaviate skip)
- `sync-service/tests/test_setup_field_weights.py` — 7 tests: validation accepts valid keys, rejects unknown keys, backward compat (empty/missing), prompt schema, prompt instruction, response shape

All 21 tests failed before implementation (as expected). 246 pre-existing tests passed.

### Task 2: GREEN — api/search.py implementation (commit c870efe)

Modified `sync-service/api/search.py`:

1. **Imports added:** `Literal` from typing; `_get_omw_synonyms`, `_ensure_omw_downloaded` from `vector_stores.synonyms`; `_apply_fuzzy_expansion` from `vector_stores.fuzzy`; `qdrant_store as _qdrant_store_mod` from `vector_stores`; `_limiter as limiter` from `api.auth`

2. **?match_mode= query param:** `Annotated[Optional[Literal["and","or"]], Query(alias="match_mode")]` added alongside `search_mode_override`; forwarded as `match_mode_override=match_mode_override` to `vector_store.search()`

3. **Query expansion pipeline (after yaml-synonyms):**
   - OMW block: guarded by `engine=="qdrant" and cfg.vector_store.fts.use_omw`, calls `_ensure_omw_downloaded + _get_omw_synonyms` per token; appends new lemmas
   - Fuzzy block: guarded by `engine=="qdrant"`, reads `_qdrant_store_mod._fuzzy_vocab.get(collection, frozenset())`, calls `_apply_fuzzy_expansion`; logs change

4. **NEW GET /search/suggest endpoint:**
   - Path: `/search/suggest`
   - Auth: `Depends(get_current_user)` (T-23-04-03)
   - Rate limit: `@limiter.limit("60/minute")` — explicit, auditable (T-23-04-04, W2)
   - Path traversal guard: `_COLLECTION_RE.match(collection)` **before** engine check (deviation from PATTERNS.md — guard must fire regardless of engine)
   - Engine guard: returns `[]` if not `VECTOR_STORE_ENGINE==qdrant`
   - Scroll: `vector_store._client.scroll` with `MatchText` filter on `_fts_text`; deduplicates via `seen: set[str]`; uses `next(iter(text_fields))` (Pitfall 6)
   - Error handling: any exception returns `[]` (no 500)

Modified `sync-service/vector_stores/weaviate_store.py`:
- Added `match_mode_override: str | None = None` kwarg to `search()` with docstring note; silently ignored for Weaviate

**Deviation (Rule 1 — Bug fix):** Path-traversal guard moved before engine check in `/search/suggest`. PATTERNS.md showed engine check first; this would silently bypass the guard for Weaviate engine calls with malicious collection names. Correct behavior: 422 always for invalid collection names.

### Task 3: GREEN — api/setup.py field_weights (commit 3d80921)

Modified `sync-service/api/setup.py`:

1. **`_build_prompt`:** Added rule 5 about `field_weights` with 0.1-1.0 assignment guidance; added `"field_weights": {{"<text_col>": 1.0, ...}}` to schema template; renumbered final rule to 6

2. **`_validate_suggested_fields`:** New loop over `suggested.get("field_weights", {})` — raises `ValueError` if any key not in `header_set` (T-23-04-05). Empty/missing `field_weights` accepted (backward compat).

3. **`suggest_config` response:** Added `"field_weights": llm_result.get("field_weights", {})` to `suggested_config` dict

### Task 4: CHECKPOINT (blocking-human)

SC-8 latency verification checkpoint — operator must confirm 3× `GET /search` requests against a 1.5M-record Qdrant Products collection each return `took_ms < 1000`. This is a blocking gate before Plan 05 frontend work begins.

## New API Surface

| Route | Auth | Rate Limit | Notes |
|---|---|---|---|
| `GET /search/suggest?q=<prefix>&collection=<name>&limit=5` | JWT required | `@limiter.limit("60/minute")` | Qdrant-only; returns `list[str]` from `_fts_text` scroll |
| `GET /search?...&match_mode=and\|or` | JWT required | global | Literal validation; forwarded as `match_mode_override` to store |

**Note for Plan 05 (frontend):**
- Suggest: `GET /api/search/suggest?q=<prefix>&collection=<name>&limit=5` → `list[str]`, JWT-protected, rate-limited at 60/min
- Match mode: `?match_mode=and|or` on `/api/search`

## Rate Limiting

`@limiter.limit("60/minute")` applied directly to `search_suggest`. Global slowapi middleware also covers it; explicit decorator makes coverage auditable (RESEARCH.md Q4 RESOLVED + W2).

## Fuzzy Vocab

`api/search.py` imports `from vector_stores import qdrant_store as _qdrant_store_mod` and reads `_qdrant_store_mod._fuzzy_vocab.get(collection, frozenset())` at query time. Vocab is populated by Plan 03 Task 3 at index time — no additional work needed in this plan.

## SC-8 Result

Not yet verified — blocked on Task 4 checkpoint. Operator must confirm `took_ms < 1000` for three queries against a 1.5M-record Qdrant collection before Plan 05 proceeds.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Path-traversal guard applied before engine check in /search/suggest**
- **Found during:** Task 2 implementation + Task 1 test failure
- **Issue:** PATTERNS.md showed engine check before `_COLLECTION_RE` guard. Test `test_path_traversal_collection_returns_422` exposed that with Weaviate engine, the engine guard returned `[]` before the path-traversal check fired, allowing malicious collection names through silently.
- **Fix:** Moved `_COLLECTION_RE.match(collection)` check to execute BEFORE the `VECTOR_STORE_ENGINE != "qdrant"` check. The 422 guard must fire regardless of which engine is active.
- **Files modified:** `sync-service/api/search.py`
- **Commit:** c870efe

**2. [Rule 3 - Blocking issue] Worktree missing configuration/ directory**
- **Found during:** Task 1 — test collection errors at import
- **Issue:** The git worktree doesn't contain the `configuration/` directory (it's in `.gitignore`). `config/settings.py` tries `sync-service/../configuration/config.yaml` but the worktree root has no such path. All tests importing `api/search.py` or `api/setup.py` errored at collection time.
- **Fix:** Created `$WT_ROOT/configuration/config.yaml` by copying from main repo. This is a runtime artifact needed for tests only.
- **Files modified:** `configuration/config.yaml` (created, not tracked in git)
- **Note:** Also fixed test `_make_config_yaml` helpers to use `vector_store:` key (not `weaviate:`) and `search_mode: fts` to avoid Ollama embedding calls in unit tests.

## Known Stubs

None — all implemented features are fully wired and tested.

## Threat Flags

None — all new surfaces are covered by the plan's threat model (T-23-04-01 through T-23-04-07).

## Self-Check: PASSED

| Check | Result |
|---|---|
| `sync-service/api/search.py` exists | FOUND |
| `sync-service/api/setup.py` exists | FOUND |
| `sync-service/vector_stores/weaviate_store.py` exists | FOUND |
| `sync-service/tests/test_suggest.py` exists | FOUND |
| `sync-service/tests/test_search_router_match_mode.py` exists | FOUND |
| `sync-service/tests/test_setup_field_weights.py` exists | FOUND |
| `.planning/phases/23-advanced-fts/23-04-SUMMARY.md` exists | FOUND |
| Commit `5244dc9` (RED tests) | FOUND |
| Commit `c870efe` (search.py impl) | FOUND |
| Commit `3d80921` (setup.py impl) | FOUND |
| 270 tests pass, 0 regressions | PASSED |
