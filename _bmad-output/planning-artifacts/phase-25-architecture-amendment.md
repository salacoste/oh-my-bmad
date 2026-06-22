# Phase 25 Architecture Amendment — Trace Correlation Live-Read Route Selection

## Decision Summary

Phase 25 may proceed from the completed Event timeline / transitions runtime-boundary proof into **Trace correlation live-read route-family planning**. This amendment selects exactly one future route family:

- `GET /v1/trace/{trace_id}`

Story 104.1 is docs/status-only. It does not authorize runtime implementation, browser network calls, backend/API expansion, test-code changes, dependencies, CI/deployment changes, services, MCP changes, generated live data, or mutation/control surfaces.

The architectural rule remains **read-only by effect**. A GET route is not sufficient: future trace runtime code must prove it cannot write, dispatch jobs, warm caches through writes, create snapshots, mutate archives/manifests, reach lifecycle/replay helpers, traverse hidden route families, or expose controls.

## Inputs

- `_bmad-output/implementation-artifacts/103-3-phase-24-epic-103-final-closure.md`
- `_bmad-output/implementation-artifacts/epic-103-retro-2026-06-22.md`
- `_bmad-output/planning-artifacts/phase-19-epics.md`
- `_bmad-output/planning-artifacts/phase-20-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-20-epics.md`
- `_bmad-output/planning-artifacts/phase-24-architecture-amendment.md`
- `dashboard/live_read_adapter.py`
- `tests/dashboard/test_event_timeline_runtime_boundary.py`
- `tests/dashboard/test_live_read_panel_contracts.py`
- `tests/dashboard/test_live_read_contracts.py`
- `tests/dashboard/test_read_only_boundary.py`

## Route selection rationale

Trace correlation is the safest next route family because:

1. It naturally follows Event timeline / transitions: event and transition rows can display `trace_id` as provenance metadata.
2. It is one route family with one required identifier: `trace_id`.
3. It is already represented in dashboard live-read metadata as `GET /v1/trace/{trace_id}`.
4. It is narrower than history/replay, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, or generated live data.
5. It can be planned first without authorizing trace runtime wiring until selector, provenance, and semantic-drift guardrails are explicit.

## Architectural boundaries

### Boundary 1 — Story 104.1 is docs/status-only

Story 104.1 may create or update only:

- `_bmad-output/implementation-artifacts/epic-103-retro-2026-06-22.md`
- `_bmad-output/planning-artifacts/phase-25-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-25-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-25-epics.md`
- `_bmad-output/implementation-artifacts/104-1-trace-correlation-live-read-route-selection.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

Story 104.1 must not edit runtime code, dashboard HTML, dashboard JavaScript, dashboard tests, API/backend code, CI, dependencies, lockfiles, scripts, deployment files, package manifests, services, MCP servers, or generated live data.

### Boundary 2 — Exact route family only

Future Phase 25 runtime work may target only:

- `GET /v1/trace/{trace_id}`

It may not silently include trace search/list, task events, task transitions, task detail, history, replay, snapshots, lifecycle readiness, aggregate overview, session list, task list, task search, task discovery, digest, stream, generated live data, or control routes.

### Boundary 3 — Explicit trace identifier semantics

Future trace runtime code must require and display `trace_id`. A trace ID may be sourced only from a visible explicit trace value, such as returned event/transition row metadata or an intentionally visible operator-provided trace value approved by the implementation story. It may not discover traces by scraping, listing, event lookup, task lookup, session traversal, aggregate synthesis, log parsing, event-spine guessing, storage, URL query/hash, hidden `data-*` selectors, or hidden task/search calls.

### Boundary 4 — Adjacent identifiers are metadata, not selectors

Future trace rows may display linked `event_id`, `task_id`, and `session_id` as returned provenance metadata. Story 104.1 does not authorize those identifiers as trace route inputs, hidden filters, join keys, discovery sources, replay/history lookup keys, lifecycle lookup keys, or aggregate/session traversal keys.

### Boundary 5 — No history/replay/lifecycle semantic drift

Future Trace correlation runtime code must render only the trace payload returned by the approved route. It must not enrich, join, infer, summarize, or traverse through history, replay, lifecycle-readiness, session, aggregate, digest, generated live data, task-list/search/discovery, event timeline, or transition sources. The panel may show returned metadata, but it must not become a history viewer, replay viewer, lifecycle controller, task discovery surface, aggregate explorer, or digest generator.

### Boundary 6 — No hidden writes or side effects

Future implementation tests must fail on:

- writer imports or calls;
- lifecycle apply/prune/helper imports;
- replay execution helpers or traversal jobs;
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

### Boundary 8 — Deferred route families remain unavailable

Phase 25 does not approve history/replay, lifecycle readiness, task-list/search/discovery, aggregate overview, session-list, or digest contracts. Those surfaces remain unavailable/needs-contract or excluded until separate architecture and product gates select them explicitly.

## Required future test strategy

A later runtime story must add tests before or with implementation that prove:

1. Exact route allowlist: `/v1/trace/{trace_id}` only.
2. GET-only requests with no body.
3. Visible trace_id source; no hidden `data-*`, query/hash, storage, task-list/search, event lookup, session traversal, aggregate, history, replay, lifecycle, or discovery source.
4. No reachability for history/replay, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, or mutation/control routes.
5. Selector-drift tests prove `event_id`, `task_id`, and `session_id` are display/provenance metadata only and cannot become route selectors, hidden filters, history/replay lookup keys, lifecycle lookup keys, aggregate/session lookup keys, or discovery sources.
6. Semantic-drift tests prove the panel does not enrich, join, infer, summarize, or traverse through history, replay, lifecycle, session, aggregate, digest, generated live data, event timeline, transitions, or discovery sources.
7. Empty/unavailable trace data renders explicit empty/unavailable state, not failure or fabricated data.
8. Invalid-shape, stale, partial, unauthorized, non-2xx, backend-unavailable, and network-failure cases render bounded non-authoritative copy.
9. Source route, trace_id, freshness/retrieved-at, authority, linked identifiers, and degraded-state metadata remain visible.
10. Static no-hidden-write/import grep guards pass.
11. Existing health, task-detail, and event/transition runtime-boundary tests remain green.

## Review requirements

- Architect must confirm the selected family stays trace-scoped, does not imply trace discovery/search/listing, and contains explicit selector/semantic-drift gates for `event_id`, `task_id`, `session_id`, history, replay, lifecycle, session, aggregate, and digest boundaries.
- Critic must confirm history/replay, lifecycle readiness, task-list/search/discovery, and aggregate/session/digest remain fail-closed and that Story 104.1 is not runtime authorization.
- Future runtime completion requires independent code-reviewer APPROVE, architect CLEAR, proportional QA or explicit QA skip rationale, push, and remote CI green.

## Changed-file expectation for Story 104.1

Product changed files should be limited to:

- `_bmad-output/implementation-artifacts/epic-103-retro-2026-06-22.md`
- `_bmad-output/planning-artifacts/phase-25-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-25-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-25-epics.md`
- `_bmad-output/implementation-artifacts/104-1-trace-correlation-live-read-route-selection.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

Workflow-only planning/review/checkpoint evidence may remain under `.omx/`.
