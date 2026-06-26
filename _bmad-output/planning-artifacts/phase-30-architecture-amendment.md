# Phase 30 Architecture Amendment — Aggregate Task List Route Selection Planning

## Decision summary

Phase 30 may proceed from completed task log digest runtime closure into the next dashboard route-family planning branch. This amendment selects:

- **Family:** aggregate task list read
- **Exact future candidate surface:** `GET /v1/tasks`

Story 109.1 is docs/status-only. It does not authorize runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, session list/detail, digest streaming, task-list/search/discovery, search filters, hidden selectors, automatic drill-down, cache warming, polling/timers/background jobs, local/session storage, browser-side LLM generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Inputs

- `_bmad-output/implementation-artifacts/108-3-phase-29-epic-108-final-closure.md`
- `_bmad-output/planning-artifacts/phase-29-epics.md`
- `docs/api-contracts.md`
- `docs/feature-status.md`
- `dashboard/live_read_adapter.py`
- `tests/dashboard/test_live_read_contracts.py`
- `.omx/context/recommended-next-step-1-open-phase-30-with-prd-a-20260625T232526Z.md`
- `.omx/interviews/phase-30-aggregate-task-list-planning-deep-interview.md`
- `.omx/specs/phase-30-aggregate-task-list-planning-ralplan.md`
- `.omx/specs/phase-30-aggregate-task-list-planning-test-spec.md`

## Route selection rationale

The remaining deferred set named by the activation prompt is aggregate task list read, session list/detail read, digest stream, and task-list/search/discovery. `GET /v1/tasks` is selected because it is the smallest overview-style candidate that can remain separable from broad discovery/search. It can be constrained to bounded server-returned task summaries and explicit list metadata. It is less coupled than session list/detail, which invites session traversal, and less operationally risky than digest streaming, which invites EventSource/WebSocket behavior, background refresh, provider availability assumptions, and generated live-data drift.

This selection does not assert that `GET /v1/tasks` is currently implemented in the HTTP API. `docs/api-contracts.md` currently documents `POST /v1/tasks` for task creation and task-scoped GET routes; `dashboard/live_read_adapter.py` currently marks `/v1/tasks` as `needs-separate-contract`. Story 109.2 must therefore prove or implement the exact read contract tests-first before any dashboard runtime completion claim.

## Architectural boundaries

### Boundary 1 — Story 109.1 is docs/status-only

Story 109.1 may create or update only Phase 30 planning artifacts, the Story 109.1 artifact, derivative feature status, sprint status, and OMX workflow evidence. It must not edit runtime code, dashboard HTML/JS behavior, tests, API/backend code, CI, dependencies, lockfiles, scripts, deployment files, package manifests, services, MCP servers, or generated live data.

### Boundary 2 — Exact selected future surface only

Future Phase 30 work may target only `GET /v1/tasks` as a read-only aggregate task summary list. It may not silently include `POST /v1/tasks`, task creation, `/v1/tasks/{task_id}` task detail calls, digest/history/trace/replay calls derived from list rows, `/v1/sessions`, `/v1/sessions/{session_id}`, `/v1/tasks/{task_id}/logs/digest/stream`, search/discovery endpoints, broad dashboard wiring, generated live data, or mutation/control routes.

### Boundary 3 — Aggregate rows are display data, not hidden selectors

Future dashboard calls must treat returned task rows as bounded display output unless a later story authorizes an explicit visible operator action. List rows must not automatically drive task detail, digest, history, trace, replay, session traversal, mutation controls, search/discovery inputs, hidden prompts, or generated live-data substrates.

### Boundary 4 — Pagination and freshness are explicit

Future work must define and test server-returned or route-local limit/pagination metadata, retrieved-at timestamps, freshness/staleness, authority/provenance, request/trace/correlation id where available, and degraded-state copy. Missing or ambiguous freshness/limit information must fail closed as non-authoritative.

### Boundary 5 — No hidden discovery, refresh, storage, or side effects

Future tests must fail on search/discovery calls, query/hash/local-storage/session-storage selectors, EventSource/WebSocket/XMLHttpRequest side channels, polling/timers, cache warming, background workers, local/session storage writes, automatic refresh, automatic retry loops, POST/PUT/PATCH/DELETE calls, and mutation/control affordances.

### Boundary 6 — Session/detail/digest-stream remain separate

Session list/detail, digest streaming, task-list/search/discovery, and broad dashboard live wiring remain separate future-only surfaces. Selecting aggregate task list read does not approve session contracts, digest-stream contracts, hidden search, or task-detail drill-down.

## Required future test strategy

A later runtime/API contract story must add tests before or with implementation that prove:

1. exact route allowlist for `GET /v1/tasks` only;
2. GET-only and body-free dashboard calls;
3. no use of `POST /v1/tasks` or any mutation method;
4. returned rows are bounded summaries and cannot become hidden selectors or automatic drill-down inputs;
5. no query/hash/storage/session/search/discovery-derived selector;
6. no `/v1/sessions`, `/v1/sessions/{session_id}`, `/v1/tasks/{task_id}/logs/digest/stream`, task-list/search/discovery, broad dashboard wiring, or aggregate-to-session traversal;
7. missing route contract, backend unavailable, unauthorized, timeout, non-2xx, invalid response, empty list, stale list, malformed row, over-limit response, and ambiguous freshness render fail-closed non-authoritative copy;
8. source route, retrieved-at, freshness, authority/provenance, request/trace/correlation id where available, pagination/limit metadata, and degraded-state metadata are visible;
9. existing health, task-detail, event/transition, trace, history/replay, lifecycle/snapshot, snapshot-create, and digest runtime-boundary tests remain green;
10. any backend/API route work is exact, typed, additive, and covered by API contract tests before dashboard runtime completion is claimed.

## Review requirements

Future runtime completion requires independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green. Story 109.1 may skip UltraQA only if changed-file verification proves docs/status-only and code-review is clean.

Generated: 2026-06-25T23:30:38Z
