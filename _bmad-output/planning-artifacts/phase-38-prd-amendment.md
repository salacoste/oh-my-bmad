# Phase 38 PRD Amendment — Task List Pagination Route-Selection Planning

Generated: 2026-06-28T19:13:22Z

## Scope statement

Phase 38 opens the next narrow planning-only branch after Phase 37 / Epic 116 closed dashboard browser consumption for the exact canonical status+limit task-list route.

Story 117.1 is docs/status-only. It selects one candidate for later consensus and tests-first proof; it does not add runtime implementation, backend/API route implementation, dashboard JavaScript/HTML behavior changes, browser network calls, tests, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, sorting controls, free-text search, arbitrary query language, hidden selectors, row-derived traversal, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring, production credentials, or production operations.

## Selected family and exact future candidate

- **Selected family:** read-only aggregate task-list pagination / next-window API planning.
- **Selected exact future candidate surface:** backend/API route-local planning for canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`.
- **Allowed limit selector domain:** one ASCII integer task-list limit from 1 through 50 inclusive.
- **Allowed offset selector domain:** one ASCII non-negative integer offset; final implementation bounds, maximum offset, and large-offset behavior must be approved by the Phase 38 implementation plan before any runtime code changes.
- **Canonical query order:** limit first, then offset. Reversed order or additional query keys remain unauthorized unless a later consensus gate changes the contract.

## Product goals

- Select the smallest pagination-adjacent API planning step without adding browser traversal, infinite scroll, free-text search, sorting, arbitrary discovery, row-driven drill-down, or mutation/control behavior.
- Preserve completed task-list contracts: selector-free `GET /v1/tasks`, status-only `GET /v1/tasks?status={task_status}`, limit-only `GET /v1/tasks?limit={task_list_limit}`, status+limit `GET /v1/tasks?status={task_status}&limit={task_list_limit}`, and dashboard status+limit browser consumption.
- Require later Architect/Critic consensus and a tests-first implementation story before any backend/API/runtime/test-code change.

## Non-goals

- Implementing pagination, offset/cursor/page traversal, next-page links, infinite scroll, browser pagination controls, dashboard JavaScript changes, task-list sorting, free-text task search, arbitrary filters, saved searches, hidden discovery, row-derived selectors, automatic adjacent-route traversal, replay execution target selection, lifecycle mutation behavior, generated live data, browser-side generation/summarization, services/MCP/dependencies/CI/deployment changes, or production operations.
- Combining offset with status, status+limit, sort, search, cursor, page, or any additional selector in this planning slice.

## Functional requirements

- **FR316 — Selected family.** Story 117.1 selects only read-only aggregate task-list pagination / next-window API planning.
- **FR317 — Exact future candidate.** The only future runtime candidate selected by this artifact is canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`.
- **FR318 — Selector domains.** Future limit values are limited to ASCII integers from 1 through 50; future offset values must be ASCII non-negative integers with final maximum and large-offset behavior approved before implementation.
- **FR319 — Canonical request shape.** Future requests must be GET-only, bodyless, canonical limit-then-offset query order, and must not include extra/repeated query keys.
- **FR320 — Response authority.** Future response design must expose selected limit, selected offset, returned_count, has_more, next_offset/null, retrieved_at, freshness_state, authority_state, provenance, and correlation/request/trace id where available before marking rows authoritative.
- **FR321 — Deferred surfaces.** Browser pagination controls, cursor/page tokens, sorting controls, free-text search, arbitrary discovery, status+offset/status+limit+offset composition, hidden selectors, automatic drill-down, lifecycle/replay mutation, generated live data, services/MCP/dependencies/CI/deployment changes, and production operations remain unauthorized until separately planned.

## Acceptance criteria for opening Story 117.1

1. Phase 38 PRD, architecture, and epics artifacts exist and define the task-list pagination planning scope.
2. Sprint status opens Phase 38 / Epic 117 with Story 117.1 as the planning candidate and does not mark implementation complete.
3. `docs/feature-status.md` is refreshed as derivative status and does not claim pagination implementation.
4. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change as part of the opening.

## Follow-on story sequence

- Story 117.1: docs/status-only route-selection planning gate for bounded task-list pagination.
- Story 117.2: future tests-first backend/API boundary only after Story 117.1 consensus.
- Story 117.3: future final validation closure after Story 117.2 review, QA, push, and remote CI evidence.
