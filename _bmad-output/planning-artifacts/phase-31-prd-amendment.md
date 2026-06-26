# Phase 31 PRD Amendment — Session List Route Selection Planning

## Summary

Phase 31 opens the next dashboard route-family planning branch after Phase 30 closed the aggregate task list boundary. Phase 31 selects exactly one remaining deferred surface for future consideration:

- **Selected family:** session visibility
- **Selected exact future candidate surface:** `GET /v1/sessions`

Story 110.1 is docs/status-only. It does not add runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, session detail, digest streaming, task-list/search/discovery, search filters, hidden selectors, automatic drill-down, cache warming, polling/timers/background jobs, local/session storage, browser-side LLM generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Problem

The dashboard now has recorded narrow boundaries for health/readiness, task detail, event/transition, trace, history/replay, lifecycle/snapshot listing, snapshot creation authorization, task log digest, and aggregate task list. Remaining dashboard backlog still includes session list/detail, digest streaming, broader task-list/search/discovery, generated live data, and production operations.

Session visibility is valuable to operators, but it can easily broaden into session-detail traversal, hidden row-driven selectors, worktree/task traversal, generated summaries, polling/heartbeat monitoring, or broad dashboard wiring. Phase 31 starts with a planning gate so one exact candidate is selected before runtime or API work is attempted.

## Goals

- Formally open Phase 31 / Epic 110 as planning-only work.
- Select the session visibility branch from the deferred set.
- Within that branch, select exactly `GET /v1/sessions` as the future candidate surface.
- Keep session detail, digest streaming, task-list/search/discovery, broad dashboard wiring, generated live data, and mutation/control behavior fail-closed.
- Require a later tests-first Story 110.2 before any browser/runtime/API contract work.
- Require future runtime/API work to prove bounded server-returned session summaries, explicit freshness/provenance/limit discipline, read-only GET behavior, no hidden session selector propagation, no automatic drill-down, fail-closed degraded states, review, QA, push, and CI evidence.

## Out of scope for Story 110.1

- runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, session detail, digest streaming, task-list/search/discovery, search filters, hidden selectors, automatic drill-down, cache warming, polling/timers/background jobs, local/session storage, browser-side LLM generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.
- Treating static Story 89.3 session visibility copy or MCP resources as an approved HTTP session list/detail route.
- Inferring session detail or search/discovery authorization from a session-list label.

## Functional requirements

- **FR259 — Phase 31 route-family scope.** The repository records Phase 31 as the planning gate for the next dashboard route-family branch after Phase 30 / Epic 109 closure.
- **FR260 — Selected family.** Story 110.1 selects exactly session visibility and does not select digest stream, task-list/search/discovery, generated live data, or production operations.
- **FR261 — Exact future candidate.** Story 110.1 selects exactly `GET /v1/sessions` as the only future candidate in this phase.
- **FR262 — Separate implementation story.** Any dashboard/API/browser use of `GET /v1/sessions` requires a later separately approved tests-first Story 110.2.
- **FR263 — Bounded summary output only.** Future runtime work may display only bounded server-returned session summary rows and associated provenance/freshness/degraded-state metadata.
- **FR264 — No hidden selector propagation.** Future runtime work must not use session rows as hidden selectors for session detail, task detail, digest, history, trace, replay, search/discovery, mutation controls, or automatic drill-down unless a later story explicitly authorizes a visible operator action.
- **FR265 — Adjacent surfaces remain deferred.** `/v1/sessions/{session_id}`, `/v1/tasks/{task_id}/logs/digest/stream`, task-list/search/discovery, search filters, generated live data, and broad dashboard live wiring remain `needs-separate-contract` or unavailable until separately planned.
- **FR266 — No behavior change in Story 110.1.** Story 110.1 must remain docs/status-only.

## Non-functional requirements

- **NFR-S49 — Session list fail-closed safety.** Missing route contract, backend unavailable, unauthorized, timeout, non-2xx, invalid response, empty list, stale list, malformed row, unknown state, over-limit response, ambiguous freshness, unsupported pagination, or stale heartbeat metadata must render non-authoritative/unavailable copy in future runtime work without auto-retry.
- **NFR-S50 — No detail/search/session drift.** Future tests must fail on session-detail calls, search/discovery calls, query/hash/storage-derived selectors, automatic refresh, polling/timers, background workers, EventSource/WebSocket/XMLHttpRequest side channels, local/session storage writes, generated data, browser-side LLM behavior, POST/PUT/PATCH/DELETE, or mutation/control affordances.
- **NFR-O35 — Session provenance and freshness.** Future displayed session rows must expose source route, retrieved-at, freshness/staleness, authority/provenance, request/trace/correlation id where available, limit/page metadata when available, and degraded-state copy.
- **NFR-M31 — Tests-first maintainability.** Future runtime/API implementation must add boundary tests before or with any wiring and keep existing dashboard runtime suites green.

## Acceptance criteria

1. Phase 31 PRD, architecture, and epics artifacts exist and define session-list route-selection planning scope.
2. Story 110.1 artifact records selected family, exact future candidate, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status sets `current_phase: 31`, keeps Epic 109 done, opens Epic 110, marks Story 110.1 done with sequential Architect/Critic consensus evidence, and leaves Story 110.2/110.3 backlog.
4. Story 110.1 explicitly excludes runtime implementation, backend/API route implementation, session detail, digest streaming, task-list/search/discovery, broad dashboard wiring, generated live data, browser-side generation, cache warming/background jobs, mutation/control behavior, dependencies/lockfiles/CI/deployment/services/MCP, production credentials, and test/runtime code changes.
5. Follow-on Phase 31 epics sequence docs/status opening first, exact session-list runtime/API contract boundary second, final closure third.

Generated: 2026-06-26T08:50:53Z
