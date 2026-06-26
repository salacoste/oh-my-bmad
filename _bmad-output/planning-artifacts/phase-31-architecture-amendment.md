# Phase 31 Architecture Amendment — Session List Route Selection Planning

## Decision summary

Phase 31 may proceed from completed aggregate task list runtime/API closure into the next dashboard route-family planning branch. This amendment selects:

- **Family:** session visibility
- **Exact future candidate surface:** `GET /v1/sessions`

Story 110.1 is docs/status-only. It does not authorize runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, session detail, digest streaming, task-list/search/discovery, search filters, hidden selectors, automatic drill-down, cache warming, polling/timers/background jobs, local/session storage, browser-side LLM generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Inputs

- `_bmad-output/implementation-artifacts/109-3-phase-30-epic-109-final-closure.md`
- `_bmad-output/planning-artifacts/phase-30-epics.md`
- `docs/feature-status.md`
- `dashboard/live_read_adapter.py`
- `tests/dashboard/test_live_read_contracts.py`
- `services/registry-api/src/registry_api/test_app.py`
- `_bmad-output/implementation-artifacts/89-3-sessions-visibility-panel.md`
- `.omx/context/2-open-phase-31-epic-110-planning-only-goal-sele-20260626T084515Z.md`
- `.omx/interviews/phase-31-session-list-planning-deep-interview.md`
- `.omx/specs/phase-31-session-list-planning-ralplan.md`
- `.omx/specs/phase-31-session-list-planning-test-spec.md`

## Route selection rationale

The remaining deferred set named by the activation prompt is session list/detail, digest streaming, broader task-list/search/discovery, generated live data, and production operations. `GET /v1/sessions` is selected because it is the smallest session-visibility candidate that can remain separable from session detail and automatic drill-down. It can be constrained to bounded server-returned session summaries and explicit list metadata.

This selection does not assert that `GET /v1/sessions` is currently implemented in the HTTP API. Current dashboard/live-read evidence keeps session list/detail outside approved reads, and registry API tests assert `GET /v1/sessions` is unavailable during the aggregate task-list boundary. Story 110.2 must therefore prove or implement the exact read contract tests-first before any dashboard runtime completion claim.

## Architectural boundaries

### Boundary 1 — Story 110.1 is docs/status-only

Story 110.1 may create or update only Phase 31 planning artifacts, the Story 110.1 artifact, derivative feature status, sprint status, and OMX workflow evidence. It must not edit runtime code, dashboard HTML/JS behavior, tests, API/backend code, CI, dependencies, lockfiles, scripts, deployment files, package manifests, services, MCP servers, or generated live data.

### Boundary 2 — Exact selected future surface only

Future Phase 31 work may target only `GET /v1/sessions` as a read-only session summary list. It may not silently include `GET /v1/sessions/{session_id}`, session detail, task detail calls, digest/history/trace/replay calls derived from session rows, digest streaming, search/discovery endpoints, broad dashboard wiring, generated live data, or mutation/control routes.

### Boundary 3 — Session rows are display data, not hidden selectors

Future dashboard calls must treat returned session rows as bounded display output unless a later story authorizes an explicit visible operator action. List rows must not automatically drive session detail, task detail, digest, history, trace, replay, search/discovery inputs, mutation controls, hidden prompts, or generated live-data substrates.

### Boundary 4 — Freshness, heartbeat, and limit metadata are explicit

Future work must define and test server-returned or route-local limit/page metadata, retrieved-at timestamps, heartbeat freshness/staleness, authority/provenance, request/trace/correlation id where available, and degraded-state copy. Missing or ambiguous freshness/limit/heartbeat information must fail closed as non-authoritative.

### Boundary 5 — No hidden discovery, refresh, storage, or side effects

Future tests must fail on search/discovery calls, query/hash/local-storage/session-storage/cookie selectors, EventSource/WebSocket/XMLHttpRequest side channels, polling/timers, cache warming, background workers, local/session storage writes, automatic refresh, automatic retry loops, POST/PUT/PATCH/DELETE calls, and mutation/control affordances.

### Boundary 6 — Session detail, digest-stream, and discovery remain separate

Session detail, digest streaming, task-list/search/discovery, generated live data, and broad dashboard live wiring remain separate future-only surfaces. Selecting session list read does not approve session-detail contracts, digest-stream contracts, hidden search, or task/session drill-down.

## Required future test strategy

A later runtime/API contract story must add tests before or with implementation that prove:

1. exact route allowlist for `GET /v1/sessions` only;
2. GET-only and body-free dashboard calls;
3. no use of `GET /v1/sessions/{session_id}` or any mutation method;
4. returned rows are bounded summaries and cannot become hidden selectors or automatic drill-down inputs;
5. no query/hash/storage/cookie/session/search/discovery-derived selector;
6. no task-detail/digest/history/trace/replay traversal, digest stream, task-list/search/discovery, broad dashboard wiring, or session-detail traversal;
7. missing route contract, backend unavailable, unauthorized, timeout, non-2xx, invalid response, empty list, stale list, malformed row, over-limit response, stale heartbeat metadata, and ambiguous freshness render fail-closed non-authoritative copy;
8. source route, retrieved-at, freshness, authority/provenance, request/trace/correlation id where available, fixed limit/page metadata, and degraded-state metadata are visible;
9. existing health, task-detail, event/transition, trace, history/replay, lifecycle/snapshot, snapshot-create, digest, and aggregate task list runtime-boundary tests remain green;
10. any backend/API route work is exact, typed, additive, read-only, and covered by API contract tests before dashboard runtime completion is claimed.

## Review requirements

Future runtime completion requires independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green. Story 110.1 may skip UltraQA only if changed-file verification proves docs/status-only and code-review is clean.

Generated: 2026-06-26T08:50:53Z
