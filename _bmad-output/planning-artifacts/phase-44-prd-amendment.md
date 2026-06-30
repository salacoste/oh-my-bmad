# Phase 44 PRD Amendment — Task List Sort Browser Controls Planning

Generated: 2026-06-30T02:20:23Z

## Scope statement

Phase 44 opened the next narrow browser/dashboard task-list branch after Phase 43 / Epic 122 closed the API-local singleton sort selector `GET /v1/tasks?sort={task_sort}` with approved value `updated_at_desc_id_asc`. It is now closed locally by Story 123.3 after Story 123.2 browser-control verification, code-review APPROVE/CLEAR, UltraQA PASS, and local dashboard validation evidence.

Story 123.1 is docs/status-only. It selects and constrains one future browser/dashboard runtime candidate: visible aggregate-task-list sort controls that issue exactly `GET /v1/tasks?sort=updated_at_desc_id_asc` against the existing API-local singleton route. It does not add dashboard JavaScript/HTML behavior, browser network calls, backend/API behavior changes, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, automatic traversal, infinite scroll, free-text search, arbitrary query language, hidden selectors, row-derived traversal, URL/hash state, local/session storage, cookies, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Selected family and exact future candidate

- **Selected family:** read-only aggregate task-list browser/dashboard singleton sort consumption.
- **Selected exact future candidate surface:** visible aggregate-task-list sort controls for exactly `GET /v1/tasks?sort=updated_at_desc_id_asc`.
- **Sort selector source:** visible browser control state only; no hidden inputs, generated selectors, URL/hash/storage state, row-derived values, cookies, timers, workers, or ambient state.
- **Approved sort vocabulary for this browser increment:** singleton value `updated_at_desc_id_asc` only, matching the existing API-local route and deterministic `updated_at DESC, id ASC` ordering.
- **Route composition policy:** sort is a standalone browser read mode for this increment. Future Story 123.2 must not append status, limit, offset, cursor, page, search, or arbitrary query keys to the sort route.
- **User-action policy:** one explicit visible user action per sorted read; no automatic traversal, polling, retry loop, prefetch, background refresh, infinite scroll, or row-driven follow-up.

## Product goals

- Expose the already-implemented singleton sort API route through the smallest safe dashboard control surface.
- Preserve completed aggregate-task-list status/limit/offset/manual-navigation browser contracts.
- Keep the route obvious and auditable: exact visible singleton sort control to exact singleton sort URL.
- Require Architect approval followed by Critic approval before any tests-first dashboard implementation story.
- Keep broader sort vocabulary and sort composition for separate later planning stories.

## Non-goals

Runtime implementation, dashboard JavaScript/HTML changes, test-code changes, backend/API changes, status+sort, limit+sort, offset+sort, status+limit+sort, limit+offset+sort, status+limit+offset+sort, additional sort values, field/direction grammar, direction toggles, free-text search/discovery, arbitrary query language, hidden selectors, generated selectors, URL/hash/local/session storage, cookies, automatic traversal, automatic next-page loops, infinite scroll, background prefetch, timer/worker retry, row-derived selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, services/MCP/dependencies/CI/deployment changes, production credentials, and production operations.

## Functional requirements

- **FR360 — Selected family.** Story 123.1 selects only browser/dashboard aggregate-task-list singleton sort consumption planning.
- **FR361 — Exact future browser route.** The only future browser route selected by this phase is exact `GET /v1/tasks?sort=updated_at_desc_id_asc`.
- **FR362 — Visible selector source.** Future implementation must derive the sort value only from visible aggregate-task-list controls and reject missing, hidden, mutated, duplicated, or out-of-vocabulary sort control state as non-authoritative.
- **FR363 — Singleton vocabulary.** The only approved browser sort value is `updated_at_desc_id_asc`; broader vocabulary remains unauthorized.
- **FR364 — No sort composition.** Future implementation must not compose sort with status, limit, offset, cursor, page, search, or other query selectors.
- **FR365 — Bodyless browser read.** Future implementation must issue GET with no body, `Accept: application/json`, and `credentials: "omit"`.
- **FR366 — Strict response validation.** Future implementation must require `selected_sort: "updated_at_desc_id_asc"`, the exact sort route metadata, freshness, authority, provenance, request/trace/correlation evidence, bounded row shape, returned count, `has_more`, and `next_offset: null` before authoritative rendering.
- **FR367 — Manual navigation isolation.** Future sorted reads must render in a separate singleton-sort subtree and leave existing manual previous/next offset controls and status/limit/offset state unchanged; sort+offset composition remains unauthorized.
- **FR368 — Existing contract preservation.** Existing selector-free/status/limit/status+limit/limit+offset/manual-navigation/status+limit+offset API and dashboard contracts must remain unchanged.
- **FR369 — Adjacent surfaces remain deferred.** Search/discovery, broader sort vocabulary, sort composition, hidden selectors, automatic traversal, services/MCP/dependency/CI/deployment changes, credentials, and production operations remain unauthorized.

## Non-functional requirements

- **NFR-S65 — Fail-closed browser selector.** Missing, malformed, hidden, duplicated, mutated, or out-of-vocabulary browser sort selector state must render non-authoritative and must not issue an adjacent route.
- **NFR-O47 — One visible action per read.** Sorted task-list reads must be operator-initiated by one explicit visible action and must not auto-repeat.
- **NFR-M38 — Regression isolation.** Future tests must prove the sort-control path without weakening existing aggregate-task-list status/limit/offset/manual-navigation contracts.

## Acceptance criteria for Story 123.1

1. Phase 44 PRD, architecture, and epics artifacts exist and define exact browser singleton sort-controls planning scope.
2. Story 123.1 artifact records selected family, exact future browser route, singleton sort vocabulary, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status opened Phase 44 / Epic 123, marked Story 123.1 done only after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR, and now marks Story 123.2 and Story 123.3 done after local implementation/closure evidence.
4. `docs/feature-status.md` is refreshed as derivative status and does not claim dashboard sort-control runtime implementation.
5. `docs/api-contracts.md` reflects the already-implemented Story 122.2 singleton API-local sort route while keeping browser composition and broader sort surfaces deferred.
6. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change as part of Story 123.1.

## Follow-on story sequence

- Story 123.1: docs/status-only exact browser/dashboard singleton sort-control planning and consensus.
- Story 123.2: done locally; tests-first dashboard/browser implementation for the exact approved boundary.
- Story 123.3: done locally; final validation closure with Story 123.2 review/QA and local validation evidence. This local closeout does not claim commit or remote CI evidence.
