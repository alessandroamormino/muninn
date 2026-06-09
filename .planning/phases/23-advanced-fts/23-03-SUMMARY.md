---
plan: 23-03
phase: 23-advanced-fts
status: complete
wave: 2
---

## Summary

Extended `QdrantVectorStore` with multi-sparse BM25 schema, RRF weighted fusion, AND/OR match_mode filter, fuzzy vocabulary population, and OMW synonym payload writing.

## Modified files

- `sync-service/vector_stores/qdrant_store.py` — all changes below
  - Module-level `_fuzzy_vocab`, `_FUZZY_VOCAB_CAP`, `_FUZZY_VOCAB_SCROLL_LIMIT`, `_SYNONYMS_PAYLOAD_CAP` constants
  - `create_index()`: multi-sparse schema (`sparse_{field}` keys) when >1 text_field; legacy `"sparse"` otherwise
  - `index_records()` fts/bm25 path: per-field `Document` vectors when multi-sparse; `_synonyms` payload (OMW or `[]`); calls `_build_fuzzy_vocab` after upsert
  - `search()`: added `match_mode_override` param; AND/OR `MatchText`/`MatchTextAny` pre-filter for fts/bm25; multi-field `RrfQuery(rrf=Rrf(weights=[...]))` branch
  - `_build_fuzzy_vocab()`: scrolls `_fts_text`, tokenizes, caps at 50K, stores as `frozenset`
- `sync-service/tests/test_qdrant_vector_store.py` — +32 tests (54 total, was 43 with Task 1 RED scaffold now all green)
  - `TestFieldWeights`: 7 tests for multi-sparse schema, per-field upsert, RrfQuery
  - `TestMatchMode`: 6 tests for AND/OR filter, override param, hybrid skip, must_not skip
  - `TestFuzzyVocab`: 6 tests for vocab population, tokenization, cap, error fallback
  - `TestSynonymsPayload`: 5 tests for use_omw=true/false, dedup, cap, hybrid skip

## Test delta

246 total (was 222). +24 new tests, all green.

## Notes for Plan 04

- `QdrantVectorStore.search` now accepts `match_mode_override: str | None = None` — pass through from `?match_mode=` FastAPI Query param
- `from vector_stores.qdrant_store import _fuzzy_vocab` — `dict[str, frozenset[str]]` populated after every `index_records` in fts/bm25 modes; consume via `_fuzzy_vocab.get(collection, frozenset())`
- Schema migration: collections created with single `"sparse"` need `POST /sync/full` to re-index when migrating to multi-sparse `text_fields` dict. Do NOT auto-migrate on read.
- `_synonyms` payload: every fts/bm25 record now carries `_synonyms: list[str]` (empty when `use_omw=false`); available for future query-time expansion.

## Self-Check: PASSED

246/246 tests green. All must_haves verified against codebase.
