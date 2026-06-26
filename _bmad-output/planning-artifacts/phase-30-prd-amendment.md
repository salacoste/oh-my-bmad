# Phase 30 PRD Amendment — Aggregate Task List Route Selection Planning

## Summary

Phase 30 opens the next dashboard route-family planning branch after Phase 29 closed the task log digest boundary. Phase 30 selects exactly one remaining deferred surface for future consideration:

- **Selected family:** aggregate task list read
- **Selected exact future candidate surface:** `GET /v1/tasks`

Story 109.1 is docs/status-only. It does not add runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, session list/detail, digest streaming, task-list/search/discovery, search filters, hidden selectors, automatic drill-down, cache warming, polling/timers/background jobs, local/session storage, browser-side LLM generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Problem

The dashboard now has recorded narrow boundaries for health/readiness, task detail, event/transition, trace, history/replay, lifecycle/snapshot listing, snapshot creation authorization, and task log digest. Remaining dashboard backlog still includes aggregate task list reads, session list/detail reads, digest streaming, and task-list/search/discovery. These surfaces can easily broaden into hidden selectors, discovery/search, automatic task traversal, session traversal, generated live data, or broad dashboard wiring.

Phase 30 starts with a planning gate so one exact candidate is selected before runtime or API work is attempted.

## Goals

- Formally open Phase 30 / Epic 109 as planning-only work.
- Select the aggregate task list read branch from the deferred set.
- Within that branch, select exactly `GET /v1/tasks` as the future candidate surface.
- Keep session list/detail, digest streaming, task-list/search/discovery, broad dashboard wiring, generated live data, and mutation/control behavior fail-closed.
- Require a later tests-first Story 109.2 before any browser/runtime/API contract work.
- Require future runtime/API work to prove bounded server-returned task summaries, explicit pagination/limit discipline, read-only GET behavior, no hidden task selector propagation, no automatic drill-down, fail-closed degraded states, review, QA, push, and CI evidence.

## Out of scope for Story 109.1

- runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, session list/detail, digest streaming, task-list/search/discovery, search filters, hidden selectors, automatic drill-down, cache warming, polling/timers/background jobs, local/session storage, browser-side LLM generation/summarization, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.
- Treating existing `POST /v1/tasks` task creation as a read/list contract.
- Inferring session or search/discovery authorization from an aggregate list label.

## Functional requirements

- **FR251 — Phase 30 route-family scope.** The repository records Phase 30 as the planning gate for the next dashboard route-family branch after Phase 29 / Epic 108 closure.
- **FR252 — Selected family.** Story 109.1 selects exactly aggregate task list read and does not select session list/detail, digest stream, or task-list/search/discovery.
- **FR253 — Exact future candidate.** Story 109.1 selects exactly `GET /v1/tasks` as the only future candidate in this phase.
- **FR254 — Separate implementation story.** Any dashboard/API/browser use of `GET /v1/tasks` requires a later separately approved tests-first Story 109.2.
- **FR255 — Bounded summary output only.** Future runtime work may display only bounded server-returned task summary rows and associated provenance/freshness/degraded-state metadata.
- **FR256 — No hidden selector propagation.** Future runtime work must not use aggregate rows as hidden selectors for task detail, digest, history, trace, replay, session traversal, mutation controls, or automatic drill-down unless a later story explicitly authorizes a visible operator action.
- **FR257 — Adjacent surfaces remain deferred.** `/v1/sessions`, `/v1/sessions/{session_id}`, `/v1/tasks/{task_id}/logs/digest/stream`, task-list/search/discovery, search filters, and broad dashboard live wiring remain `needs-separate-contract` or unavailable until separately planned.
- **FR258 — No behavior change in Story 109.1.** Story 109.1 must remain docs/status-only.

## Non-functional requirements

- **NFR-S47 — Aggregate list fail-closed safety.** Missing route contract, backend unavailable, unauthorized, timeout, non-2xx, invalid response, empty list, stale list, malformed row, unknown state, over-limit response, ambiguous freshness, or unsupported pagination states must render non-authoritative/unavailable copy in future runtime work without auto-retry.
- **NFR-S48 — No discovery/search/session drift.** Future tests must fail on search/discovery calls, query/hash/storage-derived selectors, session traversal, automatic refresh, polling/timers, background workers, websocket/xhr side channels, local/session storage writes, generated data, browser-side LLM behavior, POST/PUT/PATCH/DELETE, or mutation/control affordances.
- **NFR-O34 — List provenance and freshness.** Future displayed aggregate rows must expose source route, retrieved-at, freshness/staleness, authority/provenance, request/trace/correlation id where available, pagination/limit metadata when available, and degraded-state copy.
- **NFR-M30 — Tests-first maintainability.** Future runtime/API implementation must add boundary tests before or with any wiring and keep existing dashboard runtime suites green.

## Acceptance criteria

1. Phase 30 PRD, architecture, and epics artifacts exist and define aggregate task list route-selection planning scope.
2. Story 109.1 artifact records selected family, exact future candidate, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status sets `current_phase: 30`, keeps Epic 108 done, opens Epic 109, marks Story 109.1 done with sequential Architect/Critic consensus evidence, and leaves Story 109.2/109.3 backlog.
4. Story 109.1 explicitly excludes runtime implementation, backend/API route implementation, session list/detail, digest streaming, task-list/search/discovery, broad dashboard wiring, generated live data, browser-side generation, cache warming/background jobs, mutation/control behavior, dependencies/lockfiles/CI/deployment/services/MCP, production credentials, and test/runtime code changes.
5. Follow-on Phase 30 epics sequence docs/status opening first, exact aggregate-read runtime/API contract boundary second, final closure third.

Generated: 2026-06-25T23:30:38Z
