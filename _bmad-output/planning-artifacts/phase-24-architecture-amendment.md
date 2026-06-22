# Phase 24 Architecture Amendment — Event Timeline / Transitions Live-Read Route Selection

## Decision Summary

Phase 24 may proceed from the completed Task detail runtime-boundary proof into **Event timeline / transitions live-read route-family planning**. This amendment selects exactly one future route family composed of two task-scoped GET routes:

- `GET /v1/tasks/{task_id}/events`
- `GET /v1/tasks/{task_id}/transitions`

Story 103.1 is docs/status-only. It does not authorize runtime implementation, browser network calls, backend/API expansion, test-code changes, dependencies, CI/deployment changes, services, MCP changes, generated live data, or mutation/control surfaces.

The architectural rule remains **read-only by effect**. A GET route is not sufficient: future event/transition runtime code must prove it cannot write, dispatch jobs, warm caches through writes, create snapshots, mutate archives/manifests, reach lifecycle helpers, or expose controls.

## Inputs

- `_bmad-output/implementation-artifacts/102-3-phase-23-epic-102-final-closure.md`
- `_bmad-output/implementation-artifacts/epic-102-retro-2026-06-22.md`
- `_bmad-output/planning-artifacts/phase-19-epics.md`
- `_bmad-output/planning-artifacts/phase-20-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-20-epics.md`
- `_bmad-output/planning-artifacts/phase-23-architecture-amendment.md`
- `dashboard/live_read_adapter.py`
- `tests/dashboard/test_live_read_panel_contracts.py`
- `tests/dashboard/test_live_read_contracts.py`
- `tests/dashboard/test_live_read_adapter.py`

## Route selection rationale

Event timeline / transitions is the safest next route family because:

1. It is task-scoped and preserves the explicit `task_id` boundary proven by Task detail.
2. It is already represented as a dashboard live-read adapter panel family.
3. The route family is narrower than trace correlation, history, replay, lifecycle readiness, task-list/search/discovery, or aggregate/session/digest.
4. It does not require new discovery behavior; the future runtime can consume the visible selected task context.
5. It matches the Phase 19 progression that placed task event timeline work before trace correlation.

## Architectural boundaries

### Boundary 1 — Story 103.1 is docs/status-only

Story 103.1 may create or update only:

- `_bmad-output/planning-artifacts/phase-24-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-24-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-24-epics.md`
- `_bmad-output/implementation-artifacts/103-1-event-timeline-transitions-live-read-route-selection.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

The Phase 23 retrospective may also be added as `_bmad-output/implementation-artifacts/epic-102-retro-2026-06-22.md`.

Story 103.1 must not edit runtime code, dashboard HTML, dashboard JavaScript, dashboard tests, API/backend code, CI, dependencies, lockfiles, scripts, deployment files, package manifests, services, MCP servers, or generated live data.

### Boundary 2 — Exact route family only

Future Phase 24 runtime work may target only:

- `GET /v1/tasks/{task_id}/events`
- `GET /v1/tasks/{task_id}/transitions`

It may not silently include trace, history, replay, snapshots, lifecycle, aggregate overview, session list, task list, task search, task discovery, digest, stream, generated live data, or control routes.

### Boundary 3 — Explicit task identifier semantics

Future event/transition runtime code must require and display `task_id`. It may not discover tasks by scraping, listing, session traversal, aggregate synthesis, log parsing, event-spine guessing, or hidden task-list/search calls.

### Boundary 4 — Event identifiers are not route inputs

Future event rows may display event identifiers as provenance metadata, but Story 103.1 does not authorize `event_id` as route input, route selector, hidden filter, or discovery source.

### Boundary 5 — No trace/history/replay semantic drift

Future Event timeline / transitions runtime code must render only the task-scoped event and transition collections returned by the two approved routes. It must not enrich, join, infer, summarize, or link through trace, history, replay, lifecycle-readiness, session, aggregate, digest, generated live data, or discovery sources. The panel may show event row metadata, but it must not become a trace explorer, replay viewer, lifecycle controller, or task discovery surface.

### Boundary 6 — No hidden writes or side effects

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

### Boundary 7 — Runtime module graph remains closed per story

A future implementation story must name its approved module graph explicitly. Extra runtime modules, inline scripts, dynamic imports, workers, service workers, polling, streaming, storage/cache, beacons, WebSockets, EventSource, XMLHttpRequest, or hidden clients remain forbidden unless separately scoped and approved.

### Boundary 8 — Aggregate/session/digest remain unavailable

Phase 24 does not approve aggregate overview, session-list, task-list/search/discovery, or digest contracts. Those surfaces remain unavailable/needs-contract or excluded until separate architecture and product gates select them explicitly.

## Required future test strategy

A later runtime story must add tests before or with implementation that prove:

1. Exact route allowlist: `/v1/tasks/{task_id}/events` and `/v1/tasks/{task_id}/transitions` only.
2. GET-only requests with no body.
3. Visible task_id source; no hidden `data-*`, query/hash, storage, task-list/search, session, aggregate, trace, history, replay, lifecycle, or discovery source.
4. No reachability for aggregate/session/digest, trace, history/replay, lifecycle readiness, task-list/search/discovery, generated live data, or mutation/control routes.
5. Selector-drift tests prove `event_id` is display/provenance metadata only and cannot become a route selector, hidden filter, trace/history/replay lookup key, lifecycle lookup key, or discovery source.
6. Semantic-drift tests prove the panel does not enrich, join, infer, summarize, or link through trace, history, replay, lifecycle, session, aggregate, digest, generated live data, or discovery sources.
7. Empty event/transition collections render explicit empty states, not failure or fabricated data.
8. Invalid-shape, stale, unauthorized, non-2xx, backend-unavailable, and network-failure cases render bounded non-authoritative copy.
9. Source routes, task_id, freshness/retrieved-at, authority, row count or empty-state evidence, and degraded-state metadata remain visible.
10. Static no-hidden-write/import grep guards pass.
11. Existing health and task-detail runtime-boundary tests remain green.

## Review requirements

- Architect must confirm the selected family stays task-scoped, does not imply task discovery, and contains explicit selector/semantic-drift gates for `event_id`, trace, history, replay, lifecycle, session, aggregate, and digest boundaries.
- Critic must confirm aggregate/session/digest remains fail-closed and that Story 103.1 is not runtime authorization.
- Future runtime completion requires independent code-reviewer APPROVE, architect CLEAR, proportional QA or explicit QA skip rationale, push, and remote CI green.

## Changed-file expectation for Story 103.1

Product changed files should be limited to:

- `_bmad-output/implementation-artifacts/epic-102-retro-2026-06-22.md`
- `_bmad-output/planning-artifacts/phase-24-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-24-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-24-epics.md`
- `_bmad-output/implementation-artifacts/103-1-event-timeline-transitions-live-read-route-selection.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

Workflow-only planning/review/checkpoint evidence may remain under `.omx/`.
