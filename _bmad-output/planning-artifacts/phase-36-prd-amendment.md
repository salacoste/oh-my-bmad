# Phase 36 PRD Amendment — Task Status + Limit Composition Route Selection Planning

## Summary

Phase 36 opens the next dashboard/API route-selection planning branch after Phase 35 / Epic 114 closed the exact task-list-limit runtime/API boundary. Phase 36 selects exactly one bounded continuation of the task-list/search/discovery family for future consideration:

- **Selected family:** read-only task-list bounded selector composition
- **Selected exact future candidate surface:** `GET /v1/tasks?status={task_status}&limit={task_list_limit}`
- **Allowed status selector domain:** `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, or `failed`
- **Allowed limit selector domain:** an integer task_list_limit from 1 through 50 inclusive

Story 115.1 is docs/status-only. It does not add runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, arbitrary query language, offset/cursor/page traversal, sorting controls, new selector vocabularies, hidden selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Problem

Phase 30 implemented `GET /v1/tasks` as a selector-free bounded first page. Phase 34 implemented `GET /v1/tasks?status={task_status}` as one finite lifecycle-status selector. Phase 35 implemented `GET /v1/tasks?limit={task_list_limit}` as one bounded row-count selector. The remaining task-list/search/discovery family still explicitly rejects selector composition, pagination traversal, sorting, free-text search, arbitrary discovery, and mutation/control behavior.

Operators now have two independently proven bounded task-list selectors but cannot request their intersection. Phase 36 chooses the smallest useful composition: filter by one approved lifecycle status and constrain the returned first-page size with one approved bounded limit. This adds no new status values, no new limit range, no cursor/offset/page state, no sort key, and no search syntax. It remains materially safer than pagination, sorting, free-text search, arbitrary discovery, replay execution target selection, or lifecycle mutation planning.

## Goals

- Formally open Phase 36 / Epic 115 as planning-first work.
- Select the read-only status+limit composition branch without entering pagination traversal, free-text search, arbitrary discovery, replay execution, lifecycle mutation, or broad dashboard wiring.
- Within that branch, select exactly `GET /v1/tasks?status={task_status}&limit={task_list_limit}` as the only future candidate in this phase.
- Preserve existing selector-free, status-only, and limit-only task-list behavior until a later tests-first implementation story.
- Keep offset/cursor/page traversal, sorting controls, free-text search, arbitrary filters, hidden discovery, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, cache warming/background jobs, production credentials, and production operations fail-closed.
- Require a later tests-first Story 115.2 before any dashboard/API/browser/runtime contract work.

## Out of scope for Story 115.1

- Runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API route implementation, test-code changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, free-text search, arbitrary query language, offset/cursor/page traversal, pagination state, sorting controls, new filter keys, hidden selectors, automatic row drill-down, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.
- Treating currently rejected `status+limit` composition as already implemented.
- Inferring status+limit+anything, URL hash/query-state persistence, local/session storage, saved searches, background refresh, cache warming, or dashboard control wiring authorization from the route selection.

## Functional requirements

- **FR301 — Phase 36 route-family scope.** The repository records Phase 36 as the planning gate for the next dashboard/API route-family branch after Phase 35 / Epic 114 closure.
- **FR302 — Selected family.** Story 115.1 selects the read-only task-list bounded selector-composition family and explicitly rejects pagination traversal, sorting, free-text search, arbitrary discovery, replay execution target-selection, lifecycle apply/prune/rollback, and broad dashboard wiring for Phase 36.
- **FR303 — Exact future candidate.** Story 115.1 selects exactly `GET /v1/tasks?status={task_status}&limit={task_list_limit}` as the only future candidate in this phase.
- **FR304 — Reused finite selector domains only.** Future runtime/API work may accept only one `status` query key from the existing approved lifecycle status set and one `limit` query key whose value is an integer from 1 through 50 inclusive; no other query keys, repeated keys, request body, offset, cursor, page, sort, q/search, URL hash, cookies, storage, hidden fields, generated selectors, row-derived attributes, or task discovery sources are approved.
- **FR305 — Deterministic route spelling.** Future runtime/API work must document and test one canonical query composition for this slice; the approved surface is named `GET /v1/tasks?status={task_status}&limit={task_list_limit}`. Equivalent reordered query strings are not authorized unless Story 115.2 explicitly adds tests and documentation for order-insensitive parsing while preserving the same exact two keys.
- **FR306 — Same bounded row shape and order.** Future runtime/API work must preserve the bounded task summary row shape and existing order (`updated_at DESC, id ASC`) from `GET /v1/tasks`; it may add only visible selected-status and selected-limit metadata needed to establish authority/freshness.
- **FR307 — Separate implementation story.** Any dashboard/API/browser use of `GET /v1/tasks?status={task_status}&limit={task_list_limit}` requires a later separately approved tests-first Story 115.2.
- **FR308 — Adjacent surfaces remain deferred.** Offset/cursor/page traversal, sort controls, free-text search, arbitrary filters beyond the exact two selectors, hidden discovery, automatic task detail/digest/history/trace/replay/session traversal, replay execution target selection, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, cache warming/background refresh, mutation/control behavior, services/MCP/dependencies/CI/deployment changes, production credentials, and production operations remain unauthorized until separately planned.
- **FR309 — No behavior change in Story 115.1.** Story 115.1 must remain docs/status-only.

## Non-functional requirements

- **NFR-S59 — Composition fail-closed safety.** Missing/invalid status, missing/invalid limit, unsupported status values, unsupported limit values, zero/negative/fractional/non-integer limits, repeated query keys, extra query keys, request bodies, malformed responses, over-limit responses, stale/ambiguous freshness, backend unavailable, route failure/read error, unauthorized/configuration failure, or unexpected row fields must render non-authoritative/unavailable copy in future runtime work.
- **NFR-S60 — No traversal/discovery side-channel expansion.** Future tests must fail on offset/cursor/page traversal, sorting controls, free-text search, arbitrary filters, hidden selectors, URL hash/query-state persistence beyond the exact query keys, local/session storage, cookies, POST/PUT/PATCH/DELETE, automatic row-driven route calls, background refresh/polling/timers, workers/service workers, browser-side LLM/prompt generation, replay execution calls, lifecycle mutation calls, and control affordances unless a later story explicitly authorizes one exact mechanism.
- **NFR-O40 — Composition provenance and freshness.** Future displayed status+limit task-list state must expose source route, selected status, selected limit, retrieved_at, freshness_state, authority_state, provenance, request/trace/correlation id where available, returned_count/has_more metadata, and degraded-state copy.
- **NFR-M36 — Tests-first maintainability.** Future runtime/API implementation must add boundary tests before or with any wiring and keep existing selector-free, status-only, limit-only, and dashboard/API runtime suites green.

## Acceptance criteria

1. Phase 36 PRD, architecture, and epics artifacts exist and define task status+limit composition route-selection planning scope.
2. Story 115.1 artifact records selected family, exact future candidate, non-authorization statement, future test obligations, verification plan, and completion evidence.
3. Sprint status sets `current_phase: 36`, keeps Epic 114 done, opens Epic 115, marks Story 115.1 review/done only with sequential Architect/Critic consensus evidence, and leaves Story 115.2/115.3 backlog.
4. Story 115.1 explicitly excludes runtime implementation, backend/API route implementation, browser/runtime code changes, test-code changes, offset/cursor/page traversal, sorting controls, free-text search, arbitrary query language, hidden selectors, row-driven traversal, replay execution target selection, lifecycle mutation planning, broad dashboard wiring, generated live data, browser-side generation, cache warming/background jobs, mutation/control behavior, dependencies/lockfiles/CI/deployment/services/MCP, production credentials, and production operations.
5. Follow-on Phase 36 epics sequence docs/status opening first, exact status+limit runtime/API contract boundary second, final closure third.

Generated: 2026-06-28T01:35:04Z
