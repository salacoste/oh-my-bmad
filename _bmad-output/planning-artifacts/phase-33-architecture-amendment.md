# Phase 33 Architecture Amendment — Digest Stream Route Selection Planning

## Decision summary

Phase 33 may proceed from completed session-detail closure into the next narrow dashboard live-read planning branch. This amendment selects:

- **Family:** task log digest continuation
- **Exact future candidate surface:** `GET /v1/tasks/{task_id}/logs/digest/stream`

Story 112.1 is docs/status-only. It does not authorize runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, task-list/search/discovery, hidden selectors, automatic drill-down, cache warming, polling/timers/background jobs, local/session storage, browser-side LLM generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Inputs

- `_bmad-output/implementation-artifacts/111-3-phase-32-epic-111-final-closure.md`
- `_bmad-output/planning-artifacts/phase-32-epics.md`
- `docs/feature-status.md`
- `docs/api-contracts.md`
- `dashboard/live_read_adapter.py`
- `dashboard/static/task-log-digest.js`
- `tests/dashboard/test_task_log_digest_runtime_boundary.py`
- `tests/dashboard/test_read_only_boundary.py`
- `.omx/context/phase33-digest-stream-route-selection-20260626T220010Z.md`
- `.omx/interviews/phase-33-digest-stream-route-selection-deep-interview.md`

## Current brownfield state

The platform implements and documents non-streaming `GET /v1/tasks/{task_id}/logs/digest`. The dashboard adapter keeps `/v1/tasks/{task_id}/logs/digest/stream` in `EXCLUDED_ROUTE_PATTERNS`, and dashboard/runtime tests treat stream transports and adjacent stream routes as blocked. `docs/api-contracts.md` does not list a digest stream route. Therefore Phase 33 selects an unimplemented/uncontracted future candidate rather than promoting existing runtime behavior.

## Route selection rationale

`GET /v1/tasks/{task_id}/logs/digest/stream` is the smallest explicit deferred route after the completed non-streaming digest, aggregate task list, session list, and session detail boundaries. It is narrower than broad task-list/search/discovery and far narrower than dashboard-wide live wiring. It also has a clear existing guardrail surface: the stream path is already excluded, making it possible for a later story to prove a controlled promotion from excluded to approved if and only if tests define the stream contract first.

## Architectural boundaries

### Boundary 1 — Story 112.1 is docs/status-only

Story 112.1 may create or update only Phase 33 planning artifacts, the Story 112.1 artifact, derivative feature status, sprint status, and OMX workflow evidence. It must not edit runtime code, dashboard HTML/JS behavior, tests, API/backend code, CI, dependencies, lockfiles, scripts, deployment files, package manifests, services, MCP servers, or generated live data.

### Boundary 2 — Exact selected future surface only

Future Phase 33 work may target only `GET /v1/tasks/{task_id}/logs/digest/stream` as a read-only task-scoped digest stream. It may not silently include task-list/search/discovery, task detail, task history, trace/replay traversal, session traversal, generated browser summaries, mutation controls, or broad dashboard live wiring.

### Boundary 3 — Visible task_id path parameter only

Future dashboard calls must use only an explicit visible operator-provided `task_id` to construct the stream route. Query strings, request bodies, URL hashes, cookies, local/session storage, hidden inputs, generated selectors, row-derived hidden attributes, and task-list/search/discovery results are not approved selector sources.

### Boundary 4 — Transport is not pre-authorized

Selecting a stream route does not authorize EventSource, WebSocket, XMLHttpRequest fallback, workers, polling, automatic retry, automatic refresh, browser-side backoff queues, or background cache warming. A future implementation story must select and test exactly one transport and explicitly forbid alternatives.

### Boundary 5 — Bounded digest-stream payload only

Future stream output may expose bounded server-returned digest status/progress chunks, digest excerpts/summary chunks, final digest metadata, freshness, provenance, and correlation data. It must omit raw logs, event payloads, prompts, model/provider internals, token-by-token provider streams outside the approved digest contract, filesystem/resource paths, hrefs/URLs, generated browser summaries, and control hints.

### Boundary 6 — Stream lifecycle and fail-closed states

Future runtime/API work must define open, partial, final, malformed, stale, interrupted, unauthorized, backend-unavailable, provider-unavailable, timeout, and operator-close behavior. Any ambiguity must render non-authoritative degraded copy without automatic retry.

### Boundary 7 — Existing non-streaming digest remains independent

The completed non-streaming digest route remains a bounded single-response read. Selecting the stream candidate does not change its contract, does not add fallback from stream to non-streaming digest, and does not authorize auto-selection between routes.

## Required future test strategy

A later runtime/API contract story must add tests before or with implementation that prove:

1. exact API route allowlist for `GET /v1/tasks/{task_id}/logs/digest/stream` only;
2. GET-only, query-free, and body-free API/dashboard calls;
3. visible path-parameter validation and percent-encoding discipline for `task_id`;
4. exactly one approved stream transport, with EventSource/WebSocket/XMLHttpRequest/workers/polling/retry alternatives forbidden unless intentionally selected and documented;
5. bounded event/chunk framing, final message, terminal error, timeout, close/cancel, and stale-state behavior;
6. no task-list/search/discovery, task detail, history, trace, replay, session, generated data, broad dashboard wiring, or mutation/control calls;
7. omission of raw logs, event payloads, prompts, provider internals, filesystem/resource paths, hrefs/URLs, generated browser summaries, and control hints;
8. missing route contract, backend/provider unavailable, unauthorized, timeout, non-2xx, malformed chunk, unexpected keys, excessive chunk volume, interrupted stream, and stale/ambiguous freshness fail closed;
9. source route, visible selected `task_id`, connection/opened-at, last-event/retrieved-at, freshness, authority/provenance, request/trace/correlation id where available, and degraded-state metadata are visible;
10. existing health, task-detail, event/transition, trace, history/replay, lifecycle/snapshot, snapshot-create, digest, aggregate task list, session-list, and session-detail runtime-boundary tests remain green;
11. independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green are recorded before closure.

## Review requirements

Future runtime completion requires independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green. Story 112.1 may skip UltraQA only if changed-file verification proves docs/status-only and code-review is clean.

Generated: 2026-06-26T22:03:46Z
