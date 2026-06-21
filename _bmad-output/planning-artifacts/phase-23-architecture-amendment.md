# Phase 23 Architecture Amendment — Task Detail Live-Read Runtime Boundary

## Decision Summary

Phase 23 may proceed from the completed Health/readiness runtime-boundary proof into **Task detail live-read runtime-boundary planning**. This amendment selects exactly one future route family:

- `GET /v1/tasks/{task_id}`

Story 102.1 is docs/status-only. It does not authorize runtime implementation, browser network calls, backend/API expansion, test-code changes, dependencies, CI/deployment changes, services, MCP changes, generated live data, or mutation/control surfaces.

The architectural rule remains **read-only by effect**. A GET route is not sufficient: future task-detail runtime code must prove it cannot write, dispatch jobs, warm caches through writes, create snapshots, mutate archives/manifests, reach lifecycle helpers, or expose controls.

## Inputs

- `_bmad-output/implementation-artifacts/101-3-phase-22-final-validation-closure.md`
- `_bmad-output/planning-artifacts/phase-20-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-20-epics.md`
- `_bmad-output/planning-artifacts/phase-21-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-21-epics.md`
- `dashboard/live_read_adapter.py`
- `tests/dashboard/test_live_read_contracts.py`
- `tests/dashboard/test_live_read_adapter.py`
- `tests/dashboard/test_health_readiness_runtime_boundary.py`

## Route selection rationale

Task detail `GET /v1/tasks/{task_id}` is the safest next route family because:

1. It is already represented as an approved read contract in the dashboard live-read adapter metadata.
2. It has a single required identifier, `task_id`.
3. It is narrower than event timelines, transitions, trace correlation, history, replay, or lifecycle-readiness route families.
4. It does not require aggregate overview, session-list, task-list/search/discovery, digest, or external-service semantics.
5. It can reuse the Phase 22 runtime-boundary pattern while adding identifier-specific guardrails.

## Architectural boundaries

### Boundary 1 — Story 102.1 is docs/status-only

Story 102.1 may create or update only:

- `_bmad-output/planning-artifacts/phase-23-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-23-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-23-epics.md`
- `_bmad-output/implementation-artifacts/102-1-task-detail-live-read-route-selection.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

It must not edit runtime code, dashboard HTML, dashboard JavaScript, dashboard tests, API/backend code, CI, dependencies, lockfiles, scripts, deployment files, package manifests, services, MCP servers, or generated live data.

### Boundary 2 — Exact route family only

Future Phase 23 runtime work may target only:

- `GET /v1/tasks/{task_id}`

It may not silently include task events, task transitions, trace, history, replay, snapshots, digest, aggregate overview, session list, task list, task search, task discovery, lifecycle, or control routes.

### Boundary 3 — Explicit identifier semantics

Future task-detail runtime code must require and display `task_id`. It may not discover tasks by scraping, listing, session traversal, aggregate synthesis, log parsing, event-spine guessing, or hidden task-list/search calls.

### Boundary 4 — No hidden writes or side effects

Future implementation tests must fail on:

- writer imports or calls;
- lifecycle apply/prune/helper imports;
- snapshot creation;
- background job dispatch;
- idempotency writes;
- cache-warming write paths;
- archive mutation;
- manifest mutation;
- side-effectful reads;
- mutation/control vocabulary;
- POST/PUT/PATCH/DELETE dashboard calls.

### Boundary 5 — Runtime module graph remains closed per story

A future implementation story must name its approved module graph explicitly. Extra runtime modules, inline scripts, dynamic imports, workers, service workers, polling, streaming, storage/cache, beacons, WebSockets, EventSource, XMLHttpRequest, or hidden clients remain forbidden unless separately scoped and approved.

### Boundary 6 — Aggregate/session/digest remain unavailable

Phase 23 does not approve aggregate overview, session-list, task-list/search/discovery, or digest contracts. Those surfaces remain unavailable/needs-contract or excluded until separate architecture and tests approve them.

## Required future verification

Future Phase 23 implementation stories must include:

1. Exact changed-file allowlist checks matching story scope.
2. Runtime route/method allowlist tests proving only `GET /v1/tasks/{task_id}` is reachable.
3. Identifier tests proving `task_id` is required, displayed, and not sourced from hidden discovery/listing behavior.
4. No-hidden-write scans for writer/lifecycle/snapshot/job/idempotency/cache/archive/manifest/mutation imports or calls.
5. Static import/grep guards for forbidden paths and vocabulary.
6. Provenance/freshness/authority tests for task-detail displayed state.
7. Degraded-state tests for unavailable, stale, unauthorized, and backend-unavailable states.
8. Aggregate/session/digest/task-list/search/discovery exclusion tests.
9. Independent code-reviewer APPROVE and architect CLEAR.
10. GitHub Actions CI green after push.

## Handoff to Epics and Stories

The next BMAD artifact is `_bmad-output/planning-artifacts/phase-23-epics.md`. It decomposes Phase 23 into docs/status opening, task-detail runtime-boundary implementation, and final closure.
