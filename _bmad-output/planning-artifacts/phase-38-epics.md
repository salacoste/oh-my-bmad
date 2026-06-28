# Phase 38 Epics — Task List Pagination Route-Selection Planning

## Phase 38 theme

Phase 38 opens a planning-only branch for one pagination-adjacent task-list API candidate after Phase 37 closed status+limit browser consumption:

- Family: read-only aggregate task-list pagination / next-window API planning
- Exact future candidate: canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`
- Limit selector: ASCII integer 1 through 50 inclusive
- Offset selector: ASCII non-negative integer, with final maximum/large-offset behavior pending implementation-plan approval
- Scope: backend/API route-local future candidate only; no browser pagination controls or traversal

## Epic 117 — Task list pagination planning boundary

### Objective

Plan, later prove, and close a bounded API-local task-list pagination boundary without browser traversal, infinite scroll, sorting, free-text search, arbitrary discovery, hidden selectors, row-driven drill-down, replay/lifecycle mutation, broad dashboard wiring, generated live data, or production operations.

### Story 117.1 — Task list pagination route-selection planning

**Status:** opened; consensus and implementation pending.

**Intent:** Create Phase 38 PRD, architecture, epics, story artifact, sprint-status opening, and derivative feature-status refresh that select read-only aggregate task-list pagination / next-window API planning and exactly canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}` as the future candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 38 PRD amendment exists and selects read-only aggregate task-list pagination / next-window API planning.
2. Phase 38 architecture amendment defines exact route, selector domains, canonical query spelling/order, future metadata requirements, fail-closed states, and deferred surfaces.
3. Phase 38 epics file exists and sequences planning before implementation and final closure.
4. Story 117.1 artifact exists and records non-authorization, future test obligations, verification plan, and opening evidence.
5. Sprint status opens Phase 38/Epic 117 without marking implementation complete.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim pagination implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 117.2 — Task list pagination runtime/API boundary

**Status:** future after Story 117.1 consensus.

**Intent:** Future tests-first API-local implementation for exactly canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`.

### Story 117.3 — Phase 38 / Epic 117 final validation closure

**Status:** future after Story 117.2 review, QA, push, and green remote CI evidence.

**Intent:** Future docs/status final closure with commit and CI evidence.

## Dependency and sequencing notes

1. Story 117.1 must complete planning consensus before any runtime/API/test implementation.
2. Story 117.2 must remain API-local and must not add browser pagination controls, sort/search/discovery, status+offset/status+limit+offset composition, automatic row traversal, replay/lifecycle mutation, services/MCP/dependency/CI/deployment changes, or production operations.
3. Story 117.3 may run only after implementation, final review, proportional QA decision, push, and remote CI evidence exist.

Generated: 2026-06-28T19:13:22Z
