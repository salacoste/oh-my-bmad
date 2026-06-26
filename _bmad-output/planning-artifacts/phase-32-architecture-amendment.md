# Phase 32 Architecture Amendment — Session Detail Route Selection Planning

## Decision summary

Phase 32 may proceed from completed session-list runtime/API closure into the next session-visibility planning branch. This amendment selects:

- **Family:** session visibility continuation
- **Exact future candidate surface:** `GET /v1/sessions/{session_id}`

Story 111.1 is docs/status-only. It does not authorize runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, digest streaming, task-list/search/discovery, hidden selectors, automatic drill-down from session-list rows, cache warming, polling/timers/background jobs, local/session storage, browser-side LLM generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Inputs

- `_bmad-output/implementation-artifacts/110-3-phase-31-epic-110-final-closure.md`
- `_bmad-output/planning-artifacts/phase-31-epics.md`
- `docs/feature-status.md`
- `dashboard/live_read_adapter.py`
- `dashboard/static/session-list.js`
- `tests/dashboard/test_live_read_contracts.py`
- `tests/dashboard/test_live_read_state_contracts.py`
- `tests/dashboard/test_session_list_runtime_boundary.py`
- `services/registry-api/src/registry_api/routes/tasks.py`
- `services/registry-api/src/registry_api/test_app.py`
- `.omx/context/phase32-session-detail-route-selection-20260626T180632Z.md`
- `.omx/interviews/phase-32-session-detail-route-selection-deep-interview.md`

## Current brownfield state

The API currently implements `GET /v1/sessions` only. `services/registry-api/src/registry_api/routes/tasks.py` has no `@router.get("/sessions/{session_id}")` handler, and tests intentionally probe `GET /v1/sessions/s-secret` as adjacent blocked behavior. The dashboard adapter approves `/v1/sessions` while keeping `/v1/sessions/{session_id}` in a needs-separate-contract/forbidden-renderable set. `dashboard/static/session-list.js` fetches exactly `/v1/sessions` and does not perform a detail read.

## Route selection rationale

`GET /v1/sessions/{session_id}` is the smallest next session-visibility candidate after the list route. It can be constrained to one explicit path parameter and bounded Session-table metadata without needing task joins, event/log payloads, digest/history/trace/replay reads, search/discovery, generated summaries, mutation controls, or broad dashboard wiring.

This selection does not assert that the route is currently implemented. Story 111.2 must therefore prove or implement the exact read contract tests-first before any dashboard/runtime completion claim.

## Architectural boundaries

### Boundary 1 — Story 111.1 is docs/status-only

Story 111.1 may create or update only Phase 32 planning artifacts, the Story 111.1 artifact, derivative feature status, sprint status, and OMX workflow evidence. It must not edit runtime code, dashboard HTML/JS behavior, tests, API/backend code, CI, dependencies, lockfiles, scripts, deployment files, package manifests, services, MCP servers, or generated live data.

### Boundary 2 — Exact selected future surface only

Future Phase 32 work may target only `GET /v1/sessions/{session_id}` as a read-only session detail. It may not silently include session mutation, session search, historical session discovery, task detail, digest, history, trace, replay, digest streaming, generated live data, browser-side summarization, or broad dashboard live wiring.

### Boundary 3 — Visible session_id path parameter only

Future dashboard calls must use only an explicit visible operator-provided `session_id` to construct `GET /v1/sessions/{session_id}`. Query strings, request bodies, URL hashes, cookies, local/session storage, hidden inputs, generated selectors, or row-driven hidden attributes are not approved selector sources.

### Boundary 4 — Session-list rows remain inert

The completed session-list surface remains bounded display output. Selecting session detail does not authorize automatic row click handlers, hidden data attributes, hyperlinks, prefetching, automatic drill-down, detail polling, or use of list rows as hidden task/session/digest/history/trace/replay selectors.

### Boundary 5 — Bounded Session-table-only output

Future API detail output may expose only bounded `Session` table metadata such as `session_id`, `task_id`, `worker_kind`, `status`, `started_at`, `ended_at`, `last_heartbeat_at`, derived `heartbeat_state`, and route metadata. It must omit raw `worktree_path`, filesystem/resource paths, event payloads, logs, summaries, hrefs/URLs, generated text, operation hints, control affordances, and joined Task/Event data.

### Boundary 6 — Fail-closed degraded states

Future dashboard runtime must render non-authoritative copy for missing/invalid visible session_id, backend unavailable, unauthorized, timeout, not found, non-JSON, malformed JSON, invalid metadata, stale or ambiguous freshness, unexpected keys, over-broad payload, path-like strings, and any adjacent-route leakage.

### Boundary 7 — No side channels or background behavior

Future tests must fail on EventSource/WebSocket/XMLHttpRequest side channels, workers/service workers, polling/timers, automatic retry, automatic refresh, cache warming, storage writes, browser-side LLM/prompt generation, POST/PUT/PATCH/DELETE, and mutation/control affordances.

## Required future test strategy

A later runtime/API contract story must add tests before or with implementation that prove:

1. exact API route allowlist for `GET /v1/sessions/{session_id}` only;
2. GET-only, query-free, and body-free API/dashboard calls;
3. visible path-parameter validation and percent-encoding discipline;
4. no session-list row click/prefetch/hidden selector propagation;
5. no task detail, digest, history, trace, replay, digest stream, search/discovery, generated data, broad dashboard wiring, or mutation/control calls;
6. Session-table-only bounded response and omission of raw `worktree_path`, paths, event/log payloads, summaries, links, URLs, and control hints;
7. missing route contract, backend unavailable, unauthorized, timeout, not found, non-2xx, invalid response, stale/ambiguous freshness, unexpected keys, malformed row, and path-like values fail closed;
8. source route, visible selected session_id, retrieved-at, freshness, authority/provenance, request/trace/correlation id where available, and degraded-state metadata are visible;
9. existing health, task-detail, event/transition, trace, history/replay, lifecycle/snapshot, snapshot-create, digest, aggregate task list, and session-list runtime-boundary tests remain green;
10. independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green are recorded before closure.

## Review requirements

Future runtime completion requires independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green. Story 111.1 may skip UltraQA only if changed-file verification proves docs/status-only and code-review is clean.

Generated: 2026-06-26T18:14:59Z
