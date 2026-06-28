# Phase 35 PRD Amendment — Task List Limit Route Selection Planning

## Summary

Phase 35 opens the next dashboard/API route-selection planning branch after Phase 34 / Epic 113 closed the exact task status filter runtime/API boundary. Phase 35 selects exactly one low-risk continuation of the task-list/search/discovery family for future consideration:

- **Selected family:** read-only task-list sizing / bounded-list control
- **Selected exact future candidate surface:** `GET /v1/tasks?limit={task_list_limit}`
- **Allowed selector domain:** an integer task_list_limit from 1 through 50 inclusive

Story 114.1 is docs/status-only. It does not add runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, arbitrary query language, offset/cursor/page traversal, sorting controls, status+limit combinations, hidden selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Problem

Phase 30 implemented `GET /v1/tasks` as a selector-free bounded first page with a fixed server limit of 50. Phase 34 implemented `GET /v1/tasks?status={task_status}` as a finite lifecycle-status selector while keeping pagination, sorting, free-text search, arbitrary discovery, and combined selectors unauthorized. The remaining task-list/search/discovery family is still explicitly deferred because broad discovery can drift into hidden selectors, query-language design, automatic task traversal, pagination authority, sorting semantics, or operational controls.

Phase 35 chooses the smallest useful continuation: an explicit bounded page-size selector for the already-existing task-summary list. A limit-only read is narrower than offset/cursor pagination, sorting, free-text search, arbitrary discovery, replay execution target selection, or lifecycle mutation planning. It changes only how many bounded summary rows may be returned on the first page, leaves ordering unchanged, and must still be separately planned and tests-first because it changes the `GET /v1/tasks` selector contract.

## Goals

- Formally open Phase 35 / Epic 114 as planning-first work.
- Select the read-only task-list sizing branch without entering pagination traversal, free-text search, replay execution, lifecycle mutation, or broad dashboard wiring.
- Within that branch, select exactly `GET /v1/tasks?limit={task_list_limit}` as the only future candidate in this phase.
- Keep current exact `GET /v1/tasks` and `GET /v1/tasks?status={task_status}` behavior unchanged until a later implementation story.
- Keep offset/cursor/page traversal, sorting controls, free-text search, arbitrary filters, status+limit combinations, hidden discovery, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, cache warming/background jobs, production credentials, and production operations fail-closed.
- Require a later tests-first Story 114.2 before any dashboard/API/browser/runtime contract work.

## Out of scope for Story 114.1

- Runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, multi-field filtering, arbitrary query language, offset/cursor/page traversal, pagination state, sorting controls, status+limit combinations, hidden selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.
- Treating current `GET /v1/tasks` fixed limit metadata as already accepting a client-selected limit.
- Inferring offset/cursor pagination, infinite scroll, dashboard page-size widgets, URL hash/query-state persistence, local/session storage, saved searches, background refresh, cache warming, or status-filter composition authorization from the route selection.

## Functional requirements

- **FR293 — Phase 35 route-family scope.** The repository records Phase 35 as the planning gate for the next dashboard/API route-family branch after Phase 34 / Epic 113 closure.
- **FR294 — Selected family.** Story 114.1 selects the read-only task-list sizing / bounded-list-control family and explicitly rejects free-text search, arbitrary discovery, replay execution target-selection, lifecycle apply/prune/rollback, and broad dashboard wiring for Phase 35.
- **FR295 — Exact future candidate.** Story 114.1 selects exactly `GET /v1/tasks?limit={task_list_limit}` as the only future candidate in this phase.
- **FR296 — Limit selector only.** Future runtime/API work may accept only one `limit` query key whose value is an integer from 1 through 50 inclusive; no other query keys, repeated keys, request body, offset, cursor, page, sort, status combination, URL hash, cookies, storage, hidden fields, generated selectors, row-derived attributes, or task discovery sources are approved.
- **FR297 — Same bounded row shape and order.** Future runtime/API work must preserve the bounded task summary row shape and existing order (`updated_at DESC, id ASC`) from `GET /v1/tasks`; it may add only visible selected-limit metadata needed to establish authority/freshness.
- **FR298 — Separate implementation story.** Any dashboard/API/browser use of `GET /v1/tasks?limit={task_list_limit}` requires a later separately approved tests-first Story 114.2.
- **FR299 — Adjacent surfaces remain deferred.** Offset/cursor/page traversal, sort controls, free-text search, arbitrary filters, status+limit combinations, hidden discovery, automatic task detail/digest/history/trace/replay/session traversal, replay execution target selection, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, cache warming/background refresh, mutation/control behavior, services/MCP/dependencies/CI/deployment changes, production credentials, and production operations remain unauthorized until separately planned.
- **FR300 — No behavior change in Story 114.1.** Story 114.1 must remain docs/status-only.

## Non-functional requirements

- **NFR-S57 — Limit selector fail-closed safety.** Missing/invalid limit, unsupported limit values, zero/negative/fractional/non-integer values, repeated `limit` keys, extra query keys, request bodies, malformed responses, over-limit responses, stale/ambiguous freshness, backend unavailable, route failure/read error, unauthorized/configuration failure, or unexpected row fields must render non-authoritative/unavailable copy in future runtime work.
- **NFR-S58 — No pagination/discovery side-channel expansion.** Future tests must fail on offset/cursor/page traversal, sorting controls, free-text search, arbitrary filters, status+limit combinations, hidden selectors, URL hash/query-state persistence beyond the explicit limit query, local/session storage, cookies, POST/PUT/PATCH/DELETE, automatic row-driven route calls, background refresh/polling/timers, workers/service workers, browser-side LLM/prompt generation, replay execution calls, lifecycle mutation calls, and control affordances unless a later story explicitly authorizes one exact mechanism.
- **NFR-O39 — Limit provenance and freshness.** Future displayed limited task-list state must expose source route, selected limit, retrieved_at, freshness_state, authority_state, provenance, request/trace/correlation id where available, returned_count/has_more metadata, and degraded-state copy.
- **NFR-M35 — Tests-first maintainability.** Future runtime/API implementation must add boundary tests before or with any wiring and keep existing dashboard/API runtime suites green.

## Acceptance criteria

1. Phase 35 PRD, architecture, and epics artifacts exist and define task-list-limit route-selection planning scope.
2. Story 114.1 artifact records selected family, exact future candidate, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status sets `current_phase: 35`, keeps Epic 113 done, opens Epic 114, marks Story 114.1 review/done only with sequential Architect/Critic consensus evidence, and leaves Story 114.2/114.3 backlog.
4. Story 114.1 explicitly excludes runtime implementation, backend/API route implementation, browser/runtime code changes, test-code changes, offset/cursor/page traversal, sorting controls, free-text search, arbitrary query language, status+limit combinations, hidden selectors, row-driven traversal, replay execution target selection, lifecycle mutation planning, broad dashboard wiring, generated live data, browser-side generation, cache warming/background jobs, mutation/control behavior, dependencies/lockfiles/CI/deployment/services/MCP, production credentials, and production operations.
5. Follow-on Phase 35 epics sequence docs/status opening first, exact task-list-limit runtime/API contract boundary second, final closure third.

Generated: 2026-06-27T18:56:38Z
