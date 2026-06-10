---
phase: 23-advanced-fts
plan: "06"
subsystem: frontend-cleanup + api-hardening
tags: [uat-gap-closure, fts, suggest, react, fastapi]
dependency_graph:
  requires: ["23-05"]
  provides: ["uat-gap-01-closed", "uat-gap-02-closed"]
  affects: ["frontend/src/pages/SearchPage.tsx", "frontend/src/api/search.ts", "sync-service/api/search.py"]
tech_stack:
  added: []
  patterns: ["TDD red-green", "HTTPException 422 guard", "surgical UI removal"]
key_files:
  created: []
  modified:
    - frontend/src/api/search.ts
    - frontend/src/pages/SearchPage.tsx
    - sync-service/api/search.py
    - sync-service/tests/test_suggest.py
  deleted:
    - frontend/src/pages/search/MatchModeToggle.tsx
decisions:
  - "MatchModeToggle removed: match_mode is operator-level config (fts.match_mode in config.yaml), not a user-facing runtime choice"
  - "422 guard added before text_fields read: callers cannot silently misinterpret empty list as no suggestions"
  - "Fragment wrapper removed from SearchPage: SearchModeSelector no longer needs sibling wrapper after MatchModeToggle deletion"
metrics:
  duration: "~15 minutes"
  completed: "2026-06-10"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 4
  files_deleted: 1
---

# Phase 23 Plan 06: UAT Gap Closure (MatchModeToggle removal + suggest guard) Summary

**One-liner:** Removed MatchModeToggle UI control from SearchPage and added search_mode guard to /search/suggest that raises HTTP 422 for non-fts/bm25 Qdrant collections.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Remove MatchModeToggle from frontend | bdb3881 | frontend/src/api/search.ts, frontend/src/pages/SearchPage.tsx, deleted MatchModeToggle.tsx |
| 2 (RED) | Add failing tests for search_mode guard | cbfea45 | sync-service/tests/test_suggest.py |
| 2 (GREEN) | Add search_mode guard to /search/suggest | 430d569 | sync-service/api/search.py |

## What Was Built

### Task 1: Remove MatchModeToggle from frontend

Three surgical edits + one file deletion:

- `frontend/src/api/search.ts`: Removed `match_mode?: 'and' | 'or' | null` from `SearchParams`, removed `qs.set('match_mode', ...)` call, and removed `params.match_mode` from the `queryKey` array (now 7 items).
- `frontend/src/pages/SearchPage.tsx`: Removed MatchModeToggle import, `matchMode` state declaration, `setMatchMode('and')` from the collection-change effect, the entire second `useEffect` that reset matchMode on searchMode change, `match_mode: searchMode === 'fts' ? matchMode : null` from useSearch params, and the MatchModeToggle JSX block. The outer fragment wrapper was also removed since SearchModeSelector is now the sole child.
- `frontend/src/pages/search/MatchModeToggle.tsx`: Deleted entirely.
- Frontend build (`vite build`) exits 0 with no TypeScript errors.

### Task 2: search_mode guard in /search/suggest (TDD)

RED: Added two new test methods to `TestSuggestEndpoint`:
- `test_non_fts_search_mode_returns_422`: engine=qdrant, search_mode=hybrid → expect 422
- `test_vector_search_mode_returns_422`: engine=qdrant, search_mode=vector → expect 422

Both correctly failed before implementation (200 returned instead of 422).

GREEN: Inserted 5 lines after the `load_config()` try/except block in `sync-service/api/search.py`:
```python
search_mode = getattr(cfg.vector_store, "search_mode", None)
if search_mode not in ("fts", "bm25"):
    raise HTTPException(
        status_code=422,
        detail="suggest is only available for fts/bm25 collections",
    )
```

All 11 tests pass (9 existing + 2 new).

## Verification Results

1. `grep -r "MatchModeToggle|matchMode" frontend/src` — zero matches
2. `MatchModeToggle.tsx` — DELETED
3. `npm run build` — exits 0 (vite build successful)
4. `python3 -m pytest tests/test_suggest.py -v` — 11 passed, 0 failed
5. `grep -n "search_mode not in" sync-service/api/search.py` — guard found at line 540

## Deviations from Plan

None — plan executed exactly as written. The TDD RED/GREEN cycle was followed strictly with separate commits for the failing tests and the implementation.

## TDD Gate Compliance

- RED commit: `cbfea45` (test(23-06): add failing tests for search_mode guard)
- GREEN commit: `430d569` (feat(23-06): add search_mode guard to /search/suggest handler)
- No REFACTOR step needed (implementation was clean as written)

## Known Stubs

None.

## Threat Flags

No new threat surface introduced. The guard at T-23-06-01 was implemented as planned — callers now receive 422 (not silent []) for non-fts/bm25 collections.

## Self-Check: PASSED

Files created/modified:
- FOUND: frontend/src/api/search.ts
- FOUND: frontend/src/pages/SearchPage.tsx
- DELETED (expected): frontend/src/pages/search/MatchModeToggle.tsx
- FOUND: sync-service/api/search.py
- FOUND: sync-service/tests/test_suggest.py

Commits verified:
- FOUND: bdb3881 (Task 1: remove MatchModeToggle)
- FOUND: cbfea45 (Task 2 RED: failing tests)
- FOUND: 430d569 (Task 2 GREEN: guard implementation)
