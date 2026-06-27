# Phase 34 PRD Amendment — Task Status Filter Route Selection Planning

## Summary

Phase 34 opens the next dashboard/API route-selection planning branch after Phase 33 / Epic 112 closed the exact digest-stream runtime/API boundary. Phase 34 selects exactly one low-risk continuation of the task-list/search/discovery family for future consideration:

- **Selected family:** read-only task-list/search/discovery planning
- **Selected exact future candidate surface:** `GET /v1/tasks?status={task_status}`
- **Allowed selector domain:** one explicit task lifecycle status from `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed`

Story 113.1 is docs/status-only. It does not add runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, discovery crawling, hidden selectors, row-driven drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, production credentials, or production operations.

## Problem

Phase 30 implemented the bounded aggregate task list as exact `GET /v1/tasks` with no query string and no request body. The remaining task-list/search/discovery family is still explicitly deferred because broad discovery can drift into hidden selectors, free-text search semantics, automatic task traversal, query-language design, pagination authority, or operational controls.

Phase 34 chooses the smallest useful continuation: a single status-filtered read of the same task summary list. Status filtering is narrower than free-text search or discovery because the status vocabulary is already documented by the task lifecycle model and backed by the existing `ix_tasks_status_updated_at` index. The future implementation must still be separately planned and tests-first because it changes the `GET /v1/tasks` selector contract.

## Goals

- Formally open Phase 34 / Epic 113 as planning-first work.
- Select the read-only task-list/search/discovery branch without entering replay execution or lifecycle mutation planning.
- Within that branch, select exactly `GET /v1/tasks?status={task_status}` as the only future candidate in this phase.
- Keep current exact `GET /v1/tasks` behavior unchanged until a later implementation story.
- Keep free-text search, arbitrary filters, pagination knobs, hidden discovery, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, cache warming/background jobs, production credentials, and production operations fail-closed.
- Require a later tests-first Story 113.2 before any dashboard/API/browser/runtime contract work.

## Out of scope for Story 113.1

- Runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, multi-field filtering, arbitrary query language, pagination/offset/cursor controls, sorting controls, hidden selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.
- Treating current `GET /v1/tasks` query rejection as already supporting status filters.
- Inferring dashboard search UI, filter chips, URL hash/query-state persistence, local/session storage, saved searches, background refresh, or cache warming authorization from the route selection.

## Functional requirements

- **FR285 — Phase 34 route-family scope.** The repository records Phase 34 as the planning gate for the next dashboard route-family branch after Phase 33 / Epic 112 closure.
- **FR286 — Selected family.** Story 113.1 selects the read-only task-list/search/discovery family and explicitly rejects replay execution target-selection and lifecycle apply/prune/rollback for Phase 34.
- **FR287 — Exact future candidate.** Story 113.1 selects exactly `GET /v1/tasks?status={task_status}` as the only future candidate in this phase.
- **FR288 — Status selector only.** Future runtime/API work may accept only one `status` query key whose value is one of `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed`; no other query keys, request body, URL hash, cookies, storage, hidden fields, generated selectors, row-derived attributes, or task discovery sources are approved.
- **FR289 — Same bounded row shape.** Future runtime/API work must preserve the bounded task summary row shape from `GET /v1/tasks` and may add only visible selected-filter metadata needed to establish authority/freshness.
- **FR290 — Separate implementation story.** Any dashboard/API/browser use of `GET /v1/tasks?status={task_status}` requires a later separately approved tests-first Story 113.2.
- **FR291 — Adjacent surfaces remain deferred.** Free-text search, arbitrary filters, pagination/offset/cursor controls, sorting controls, hidden discovery, automatic task detail/digest/history/trace/replay/session traversal, replay execution target selection, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, cache warming/background refresh, mutation/control behavior, services/MCP/dependencies/CI/deployment changes, production credentials, and production operations remain unauthorized until separately planned.
- **FR292 — No behavior change in Story 113.1.** Story 113.1 must remain docs/status-only.

## Non-functional requirements

- **NFR-S55 — Selector fail-closed safety.** Missing/invalid status, unsupported status values, repeated `status` keys, extra query keys, request bodies, malformed responses, over-limit responses, stale/ambiguous freshness, backend unavailable, route failure/read error, unauthorized/configuration failure, or unexpected row fields must render non-authoritative/unavailable copy in future runtime work.
- **NFR-S56 — No discovery side-channel expansion.** Future tests must fail on free-text search, broad discovery, hidden selectors, URL hash/query-state persistence beyond the explicit status query, local/session storage, cookies, POST/PUT/PATCH/DELETE, automatic row-driven route calls, background refresh/polling/timers, workers/service workers, browser-side LLM/prompt generation, replay execution calls, lifecycle mutation calls, and control affordances unless a later story explicitly authorizes one exact mechanism.
- **NFR-O38 — Filter provenance and freshness.** Future displayed filtered task-list state must expose source route, selected status, retrieved_at, freshness_state, authority_state, provenance, request/trace/correlation id where available, bounded count/has_more metadata, and degraded-state copy.
- **NFR-M34 — Tests-first maintainability.** Future runtime/API implementation must add boundary tests before or with any wiring and keep existing dashboard/API runtime suites green.

## Acceptance criteria

1. Phase 34 PRD, architecture, and epics artifacts exist and define task-status-filter route-selection planning scope.
2. Story 113.1 artifact records selected family, exact future candidate, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status sets `current_phase: 34`, keeps Epic 112 done, opens Epic 113, marks Story 113.1 review/done only with sequential Architect/Critic consensus evidence, and leaves Story 113.2/113.3 backlog.
4. Story 113.1 explicitly excludes runtime implementation, backend/API route implementation, browser/runtime code changes, test-code changes, free-text search, arbitrary query language, pagination/sort controls, hidden selectors, row-driven traversal, replay execution target selection, lifecycle mutation planning, broad dashboard wiring, generated live data, browser-side generation, cache warming/background jobs, mutation/control behavior, dependencies/lockfiles/CI/deployment/services/MCP, production credentials, and production operations.
5. Follow-on Phase 34 epics sequence docs/status opening first, exact status-filter runtime/API contract boundary second, final closure third.

Generated: 2026-06-27T16:32:18Z
