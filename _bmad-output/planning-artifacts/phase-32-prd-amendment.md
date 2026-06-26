# Phase 32 PRD Amendment — Session Detail Route Selection Planning

## Summary

Phase 32 opens the next dashboard route-family planning branch after Phase 31 closed the exact session-list runtime/API boundary. Phase 32 selects exactly one remaining deferred surface for future consideration:

- **Selected family:** session visibility continuation
- **Selected exact future candidate surface:** `GET /v1/sessions/{session_id}`

Story 111.1 is docs/status-only. It does not add runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, digest streaming, task-list/search/discovery, hidden selectors, automatic drill-down from session-list rows, cache warming, polling/timers/background jobs, local/session storage, browser-side LLM generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Problem

Phase 31 implemented a bounded session summary list through `GET /v1/sessions` while keeping session detail deferred. Operators can now see session rows, but a detail route is still valuable for inspecting a single session's bounded registry metadata. That detail surface is risky because it can silently expand into session-row drill-down, task traversal, event/log payload retrieval, worktree/path leakage, digest/history/trace/replay calls, generated summaries, or mutation/control affordances.

Phase 32 starts with a planning gate so the exact detail route is selected without authorizing implementation until a later tests-first story proves the runtime/API boundary.

## Goals

- Formally open Phase 32 / Epic 111 as planning-first work.
- Select the session visibility continuation branch after the completed session-list boundary.
- Within that branch, select exactly `GET /v1/sessions/{session_id}` as the only future candidate in this phase.
- Keep digest streaming, task-list/search/discovery, generated live data, broad dashboard wiring, mutation/control behavior, production operations, and automatic row-driven drill-down fail-closed.
- Require a later tests-first Story 111.2 before any browser/runtime/API contract work.
- Require future runtime/API work to prove a bounded Session-table-only detail response, explicit path-parameter discipline, freshness/provenance/correlation metadata, read-only GET behavior, no query/body selectors, no hidden selector propagation, no raw worktree/path/event/log/generated output, fail-closed degraded states, review, QA, push, and CI evidence.

## Out of scope for Story 111.1

- Runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, digest streaming, task-list/search/discovery, search filters, hidden selectors, automatic drill-down, cache warming, polling/timers/background jobs, local/session storage, browser-side LLM generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.
- Treating existing session-list rows, static session copy, or MCP resources as an approved HTTP session-detail route.
- Inferring task detail, digest, history, trace, replay, search/discovery, generated summary, or mutation/control authorization from the session-detail label.

## Functional requirements

- **FR267 — Phase 32 route-family scope.** The repository records Phase 32 as the planning gate for the next dashboard route-family branch after Phase 31 / Epic 110 closure.
- **FR268 — Selected family continuation.** Story 111.1 selects exactly session visibility continuation and does not select digest stream, task-list/search/discovery, generated live data, broad dashboard wiring, or production operations.
- **FR269 — Exact future candidate.** Story 111.1 selects exactly `GET /v1/sessions/{session_id}` as the only future candidate in this phase.
- **FR270 — Separate implementation story.** Any dashboard/API/browser use of `GET /v1/sessions/{session_id}` requires a later separately approved tests-first Story 111.2.
- **FR271 — Bounded detail output only.** Future runtime work may display only bounded server-returned Session-table detail fields and associated provenance/freshness/degraded-state metadata.
- **FR272 — Visible path parameter only.** Future runtime work may use only an explicit visible operator-provided `session_id` path parameter. It must not use query strings, request bodies, hashes, storage, cookies, hidden form fields, session-list row attributes, or generated selectors to choose the detail route.
- **FR273 — No hidden selector propagation.** Future runtime work must not use session detail as a hidden selector for task detail, digest, history, trace, replay, search/discovery, mutation controls, generated prompts, or automatic drill-down unless a later story explicitly authorizes a visible operator action.
- **FR274 — Adjacent surfaces remain deferred.** `/v1/tasks/{task_id}/logs/digest/stream`, task-list/search/discovery beyond already approved exact reads, automatic row-driven traversal, generated live data, broad dashboard wiring, and mutation/control routes remain `needs-separate-contract` or unavailable until separately planned.
- **FR275 — No behavior change in Story 111.1.** Story 111.1 must remain docs/status-only.

## Non-functional requirements

- **NFR-S51 — Session detail fail-closed safety.** Missing route contract, missing/invalid visible `session_id`, backend unavailable, unauthorized, timeout, non-2xx including not-found, invalid response, stale detail, malformed row, ambiguous freshness, over-broad payload, unexpected keys, path-like values, or path/event/log/generated payload leakage must render non-authoritative/unavailable copy in future runtime work without auto-retry.
- **NFR-S52 — No traversal or path leakage.** Future tests must fail on task detail, digest, history, trace, replay, search/discovery calls, row-driven automatic route construction, raw `worktree_path`, filesystem paths, event payloads, log content, hrefs/URLs, generated summaries, POST/PUT/PATCH/DELETE, operation/control affordances, local/session storage writes, cookies, background workers, EventSource/WebSocket/XMLHttpRequest side channels, polling/timers, automatic refresh, or browser-side LLM behavior.
- **NFR-O36 — Session-detail provenance and freshness.** Future displayed session detail must expose source route, selected visible session_id, retrieved-at, freshness/staleness, authority/provenance, request/trace/correlation id where available, and degraded-state copy.
- **NFR-M32 — Tests-first maintainability.** Future runtime/API implementation must add boundary tests before or with any wiring and keep existing dashboard/API runtime suites green.

## Acceptance criteria

1. Phase 32 PRD, architecture, and epics artifacts exist and define session-detail route-selection planning scope.
2. Story 111.1 artifact records selected family continuation, exact future candidate, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status sets `current_phase: 32`, keeps Epic 110 done, opens Epic 111, marks Story 111.1 done with sequential Architect/Critic consensus evidence, and leaves Story 111.2/111.3 backlog.
4. Story 111.1 explicitly excludes runtime implementation, backend/API route implementation, browser/runtime code changes, test-code changes, automatic session-list row drill-down, task traversal, digest streaming, task-list/search/discovery, broad dashboard wiring, generated live data, browser-side generation, cache warming/background jobs, mutation/control behavior, dependencies/lockfiles/CI/deployment/services/MCP, production credentials, and production operations.
5. Follow-on Phase 32 epics sequence docs/status opening first, exact session-detail runtime/API contract boundary second, final closure third.

Generated: 2026-06-26T18:14:59Z
