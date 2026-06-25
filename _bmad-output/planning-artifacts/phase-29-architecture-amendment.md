# Phase 29 Architecture Amendment — Aggregate/Session/Digest Route Selection Planning

## Decision summary

Phase 29 may proceed from completed Snapshot Creation authorization into the next dashboard route-family planning branch. This amendment selects:

- **Family:** aggregate/session/digest
- **Exact future candidate surface:** `GET /v1/tasks/{task_id}/logs/digest`

Story 108.1 is docs/status-only. It does not authorize runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API changes, test-code changes, dependencies, CI/deployment changes, services, MCP changes, generated live data, task-list/search/discovery, aggregate/session list/detail, digest streaming, browser-side LLM generation, cache warming, mutation/control behavior, broad dashboard wiring, or production operations.

## Inputs

- `_bmad-output/implementation-artifacts/107-3-phase-28-epic-107-final-closure.md`
- `_bmad-output/planning-artifacts/phase-28-epics.md`
- `docs/api-contracts.md`
- `docs/feature-status.md`
- `dashboard/live_read_adapter.py`
- `tests/dashboard/test_live_read_contracts.py`
- `.omx/context/phase-29-aggregate-session-digest-planning-20260625T214500Z.md`
- `.omx/interviews/phase-29-aggregate-session-digest-planning-deep-interview.md`
- `.omx/specs/phase-29-aggregate-session-digest-planning-ralplan.md`
- `.omx/specs/phase-29-aggregate-session-digest-planning-test-spec.md`

## Route selection rationale

The remaining backlog named by the activation prompt has two broad families: `aggregate/session/digest` and `task-list/search/discovery`. `task-list/search/discovery` is the broader discovery/listing branch and should remain deferred. Inside `aggregate/session/digest`, aggregate task listing and session list/detail still lack an approved safe dashboard contract. `GET /v1/tasks/{task_id}/logs/digest` is already an API contract and can be constrained to a visible task_id selector, making it the narrowest candidate inside the selected high-risk family. It remains high-risk because digest output can imply LLM generation, generated live data, streaming, external-provider availability, or background refresh. Those behaviors are not authorized by Story 108.1.

## Architectural boundaries

### Boundary 1 — Story 108.1 is docs/status-only

Story 108.1 may create or update only Phase 29 planning artifacts, the Story 108.1 artifact, derivative feature status, sprint status, and OMX workflow evidence. It must not edit runtime code, dashboard HTML/JS behavior, tests, API/backend code, CI, dependencies, lockfiles, scripts, deployment files, package manifests, services, MCP servers, or generated live data.

### Boundary 2 — Exact selected future surface only

Future Phase 29 runtime work may target only `GET /v1/tasks/{task_id}/logs/digest`. It may not silently include `/v1/tasks/{task_id}/logs/digest/stream`, `/v1/tasks` aggregate/list reads, `/v1/sessions`, `/v1/sessions/{session_id}`, task-list/search/discovery, broad dashboard wiring, generated live data, mutation/control routes, or external/browser-side LLM calls.

### Boundary 3 — Visible task_id selector discipline

Future dashboard calls must derive the task_id only from an explicit visible task context already approved for task-scoped dashboard reads. Query strings, URL hashes, local/session storage, cookies, hidden forms, session metadata, trace/event/replay/snapshot metadata, task lists, search results, aggregate views, and discovery outputs must not become hidden digest selectors.

### Boundary 4 — Digest presentation is bounded output, not generated live data

Future digest output may display bounded text/metadata returned by the backend route, source route, visible task_id, retrieved-at or completed-at, freshness/staleness, authority/provenance, request/trace/correlation id where available, and degraded-state copy. Digest text must not become a route selector, control input, replay target, search/discovery input, aggregate/session source, hidden prompt, or generated live-data substrate.

### Boundary 5 — No hidden generation, streaming, refresh, or side effects

Future tests must fail on browser-side LLM calls, prompt construction, hidden summarization, digest streaming, EventSource/WebSocket/XMLHttpRequest side channels, polling/timers, cache warming, background workers, local/session storage writes, automatic refresh, automatic retry loops, POST/PUT/PATCH/DELETE calls, and any mutation/control affordance.

### Boundary 6 — Aggregate/session and discovery remain separate

Aggregate task list/read contracts, session list/detail contracts, task-list/search/discovery, and broad dashboard live wiring remain separate future-only surfaces. Selecting the digest route does not approve aggregate/session contracts or discovery/listing behavior.

## Required future test strategy

A later runtime story must add tests before or with implementation that prove:

1. exact route allowlist for `GET /v1/tasks/{task_id}/logs/digest` only;
2. GET-only and body-free dashboard calls;
3. visible task_id is the only selector;
4. no query/hash/storage/session/discovery/list/aggregate-derived selector;
5. no `/v1/tasks/{task_id}/logs/digest/stream`, `/v1/tasks`, `/v1/sessions`, `/v1/sessions/{session_id}`, task-list/search/discovery, aggregate/session traversal, or broad dashboard wiring;
6. no browser-side LLM generation, prompt construction, hidden summarization, external provider call, cache warming, polling/timers, background workers, websocket/xhr side channels, local/session storage writes, automatic refresh, or automatic retry;
7. missing task_id, unavailable/missing digest, no configured digest provider, provider unavailable, timeout, non-2xx, invalid-response, empty-digest, stale, unauthorized, and backend-unavailable states render fail-closed non-authoritative copy;
8. digest text cannot become generated live data, route selector, control input, replay target, aggregate/session data, or discovery/search input;
9. source route, task_id, retrieved-at/completed-at, freshness, authority/provenance, request/trace/correlation id where available, and degraded-state metadata are visible;
10. existing health, task-detail, event/transition, trace, history/replay, lifecycle/snapshot, and snapshot-create runtime-boundary tests remain green.

## Review requirements

Future runtime completion requires independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green. Story 108.1 may skip UltraQA only if changed-file verification proves docs/status-only and code-review is clean.
