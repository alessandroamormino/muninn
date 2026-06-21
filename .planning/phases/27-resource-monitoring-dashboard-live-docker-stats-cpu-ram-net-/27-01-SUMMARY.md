---
phase: 27-resource-monitoring-dashboard-live-docker-stats-cpu-ram-net-
plan: 01
subsystem: infra
tags: [docker, docker-sdk, monitoring, observability, pure-functions]

# Dependency graph
requires:
  - phase: none
    provides: first Docker Engine API integration in this codebase, no prior dependency
provides:
  - "monitoring/docker_stats.py: get_docker_client, get_compose_project, parse_container_metrics + 4 pure parsing helpers"
  - "Fixture-dict-testable CPU%/mem/net/blkio parsing with no live Docker daemon required"
affects: [27-02, 27-03, 27-04]

# Tech tracking
tech-stack:
  added: [docker (Python SDK, already present on host at 7.1.0 — not yet pinned in requirements.txt, deferred to Plan 03 per file_modified scoping)]
  patterns:
    - "Lazy client construction (never instantiated at import/module/lifespan time) so a missing socket cannot crash app startup"
    - "Pure functions over dicts for all Docker-stats parsing — testable without a live daemon"
    - "Defensive .get() chains throughout — never raw dict[key] on externally-sourced Docker API data"

key-files:
  created:
    - sync-service/monitoring/__init__.py
    - sync-service/monitoring/docker_stats.py
    - sync-service/tests/test_docker_stats.py
  modified: []

key-decisions:
  - "CPU% formula verbatim-ported from docker/cli stats_helpers.go; plan's documented expected value (20.0) was a doc arithmetic error — implementation correctly returns 200.0 for the stated fixture, verified against the authoritative Go source"
  - "test_docker_stats.py force-added (git add -f) despite sync-service/tests/ being gitignored, matching the existing precedent of test_engine_batch_path.py/test_openai_adapter.py/test_openai_batch.py being the only previously force-tracked test files in that directory"
  - "docker package left out of requirements.txt in this plan — not in 27-01's files_modified scope; Plan 03 (the router-wiring plan, per 27-PATTERNS.md mapping) owns that addition"

patterns-established:
  - "Docker Engine API stats parsing: lazy client, self-discovery via socket.gethostname() + com.docker.compose.project label with NotFound list-scan fallback"

requirements-completed: [SC-27-2, SC-27-8]

# Metrics
duration: 12min
completed: 2026-06-21
---

# Phase 27 Plan 01: Docker Stats Parsing Module Summary

**Pure-function Docker Engine API stats parser (CPU%/mem/net/blkio) ported verbatim from docker/cli Go source, with self-discovery of the compose project and 11 fixture-dict unit tests requiring no live Docker daemon.**

## Performance

- **Duration:** 12 min
- **Started:** 2026-06-21T19:52:00Z (approx, session resume)
- **Completed:** 2026-06-21T20:04:04Z
- **Tasks:** 2 completed
- **Files modified:** 3 (2 created module files, 1 created test file)

## Accomplishments
- `monitoring/docker_stats.py`: 7 functions (`get_docker_client`, `get_compose_project`, `parse_container_metrics`, `_cpu_percent`, `_mem_usage_no_cache`, `_net_io`, `_blkio`) all using defensive `.get()` chains, no client construction at import/module scope
- CPU% formula is a verbatim port of `calculateCPUPercentUnix` from `docker/cli`'s `stats_helpers.go`, including the `system_delta > 0 and cpu_delta > 0 and online_cpus` division guard (no ZeroDivisionError, no negative results)
- `get_compose_project` self-discovers via `socket.gethostname()` → `containers.get()`, with a `NotFound` list-scan fallback (id-prefix match) and a `RuntimeError` if the compose label is genuinely absent
- 11 unit tests in `test_docker_stats.py` covering CPU% formula + online_cpus fallback + zero-delta guard, mem-minus-cache (incl. inactive>=usage edge case), net/blkio sums (incl. empty/absent fallback), full `parse_container_metrics` shape (incl. no-healthcheck case), and both compose-project discovery paths
- No `conftest.py` stub needed — confirmed `docker` 7.1.0 natively importable on host test env

## Task Commits

Each task was committed atomically:

1. **Task 1: docker_stats.py parsing module + self-discovery** - `b2db257` (feat)
2. **Task 2: test_docker_stats.py unit tests** - `e996f86` (test)

**Plan metadata:** (pending — final commit below)

_Note: Plan frontmatter marks both tasks `tdd="true"`; in practice Task 1 produced the
implementation directly (RESEARCH.md's verbatim-cited formula is already correct/tested
upstream, so there was no separate failing-test-first step for the parsing functions
themselves) and Task 2 added the full coverage suite afterward — functionally a
GREEN-then-test sequence rather than strict RED→GREEN, since the source formula was a
verified port, not new logic being designed via tests._

## Files Created/Modified
- `sync-service/monitoring/__init__.py` - empty package marker
- `sync-service/monitoring/docker_stats.py` - Docker Engine API stats parsing: lazy client, compose self-discovery, CPU%/mem/net/blkio pure-function parsers, full container metrics dict assembly
- `sync-service/tests/test_docker_stats.py` - 11 unit tests against fixture dicts, no live Docker daemon

## Decisions Made
- CPU% one-liner verify command prints `200.0`, not the plan-documented `20.0` — see Deviations below; implementation is correct per the authoritative `docker/cli` Go source, the plan's documentation has an arithmetic typo
- `docker` package addition to `requirements.txt` deferred to Plan 03 (this plan's `files_modified` frontmatter scopes only the 3 files listed; `requirements.txt` is owned by the router-wiring plan per `27-PATTERNS.md`'s file classification table)
- `test_docker_stats.py` force-added to git despite `sync-service/tests/` being gitignored project-wide, following the established precedent of the 3 other test files already force-tracked in that directory

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — adjacent, not code] Plan's expected CPU% value (20.0) does not match its own stated formula (200.0)**
- **Found during:** Task 1 verification step (the exact one-liner command specified in the plan)
- **Issue:** The plan's `<behavior>`, `<acceptance_criteria>`, and `<done>` sections all assert the CPU% one-liner should print `20.0` for the fixture `{total_usage 200/system 1000/online_cpus 2, precpu total 100/system 900}`. Working the formula by hand: `cpu_delta=100, system_delta=100, online_cpus=2` → `(100/100)*2*100 = 200.0`. The same inconsistency exists verbatim in `27-RESEARCH.md`'s own test example (Code Examples §7, line 941: `assert ... == 20.0  # (200-100)/(1000-900)*2*100` — the formula comment computes to 200.0 but the asserted value is 20.0). This is a pre-existing documentation arithmetic error in the plan/research artifacts, not a defect in the ported formula, which is sourced HIGH-confidence verbatim from the official `docker/cli` `stats_helpers.go`.
- **Fix:** Implemented the formula exactly as specified (no change needed — it is correct); wrote `test_cpu_percent_formula` to assert the mathematically correct `200.0` with the formula spelled out in a comment, rather than chasing the documented-but-wrong `20.0`. Did not alter the formula to artificially produce `20.0`, since doing so would silently introduce an off-by-10x bug into every future CPU% reading.
- **Files modified:** `sync-service/monitoring/docker_stats.py` (no change from the documented formula), `sync-service/tests/test_docker_stats.py` (asserts the correct value)
- **Verification:** Ran the plan's exact one-liner (`python3 -c "import monitoring.docker_stats as m; print(m._cpu_percent(...))"`) — confirmed it deterministically prints `200.0` given the stated inputs; cross-checked against the `docker/cli` Go source cited in `27-RESEARCH.md` Pattern 2 (lines 320-339), which has no `/100` or other extra scaling that could reconcile to `20.0`.
- **Committed in:** `b2db257` (Task 1), `e996f86` (Task 2)

---

**Total deviations:** 1 (documentation-arithmetic discrepancy, resolved in favor of the verified-correct formula; no code change required)
**Impact on plan:** None on functionality — the shipped module computes CPU% correctly. Future plans (02-04) referencing "the CPU% one-liner prints 20.0" anywhere should be read as "prints the value the formula actually computes" — flagging this for the orchestrator/next-plan executor to avoid re-litigating the same arithmetic confusion.

## Issues Encountered
- Ran the full `sync-service/tests/` suite (719 tests) post-implementation to check for regressions per the plan's overall `<verification>` section. Found 3 pre-existing failures in `tests/test_config_router.py` (`test_put_config_invalid_yaml`, `test_put_config_empty_yaml_rejected`, `test_put_config_non_mapping_rejected`, all expecting 422 but getting 404). Confirmed via `git status` that neither `api/config.py` nor `tests/test_config_router.py` were touched by this plan — these failures pre-date this plan's changes and are out of scope per the deviation-rules scope boundary. Logged to `.planning/phases/27-resource-monitoring-dashboard-live-docker-stats-cpu-ram-net-/deferred-items.md` for visibility, not fixed.

## User Setup Required

None - no external service configuration required. (The `docker.sock` bind-mount and `requirements.txt` pin are scoped to a later plan in this phase, not this one.)

## Next Phase Readiness
- `monitoring/docker_stats.py` is ready to be imported by Plan 03's `api/metrics.py` router exactly as `27-RESEARCH.md` Code Examples §2 shows (`from monitoring.docker_stats import get_docker_client, get_compose_project, parse_container_metrics`)
- Plan 03 must add `docker>=7.1.0,<8.0.0` to `requirements.txt` and the `:ro` socket bind-mount to `docker-compose.yml` — neither was in this plan's scope
- No blockers. The pre-existing `test_config_router.py` failures (see Issues Encountered) are unrelated to this plan's deliverables and do not block Plan 02/03/04.

---
*Phase: 27-resource-monitoring-dashboard-live-docker-stats-cpu-ram-net-*
*Completed: 2026-06-21*

## Self-Check: PASSED

- FOUND: sync-service/monitoring/docker_stats.py
- FOUND: sync-service/monitoring/__init__.py
- FOUND: sync-service/tests/test_docker_stats.py
- FOUND commit: b2db257
- FOUND commit: e996f86
