# Phase 41 Epics — Task Status + Limit + Offset API-local Route Composition Planning

Generated: 2026-06-29T13:41:34Z

## Phase 41 theme

Phase 41 opens the bounded API-local route-composition branch for task-list reads:

- Family: read-only aggregate task-list API-local bounded selector composition
- Exact future candidate: canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}` only
- Status selector: existing finite lifecycle status vocabulary
- Limit selector: ASCII integer 1 through 50 inclusive
- Offset selector: ASCII non-negative integer 0 through 2147483647 inclusive, raw spelling 1-10 ASCII digits
- Scope: docs/status-only planning first; no runtime/API/test implementation until Architect then Critic consensus

## Epic 120 — Task status + limit + offset API-local boundary

### Objective

Plan, prove, implement, and close a bounded API-local task-list route composition that returns one explicit filtered window for one lifecycle status, one bounded limit, and one bounded offset without dashboard/browser expansion, automatic traversal, search/sort/discovery, hidden selectors, row-driven drill-down, replay/lifecycle mutation, broad wiring, generated live data, or production operations.

### Story 120.1 — Task status + limit + offset API-local planning

**Status:** done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus.

**Intent:** Create Phase 41 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and consensus evidence selecting exact canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}` as the future API-local runtime candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 41 PRD amendment exists and selects read-only aggregate task-list API-local status+limit+offset route-composition planning.
2. Phase 41 architecture amendment defines exact route, selector domains, canonical query order, filtered window semantics, response metadata, fail-closed selector grammar, and deferred surfaces.
3. Phase 41 epics file exists and sequences planning before implementation and final closure.
4. Story 120.1 artifact exists and records non-authorization, future test obligations, verification plan, consensus evidence, and completion evidence.
5. Sprint status opens Phase 41/Epic 120, marks Story 120.1 done only after Architect/Critic consensus, and keeps implementation future work.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim status+limit+offset implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 120.2 — Task status + limit + offset API-local runtime boundary

**Status:** implemented locally / in review after Story 120.1 consensus; final completion awaits clean code-review and UltraQA gates.

**Intent:** Tests-first API-local implementation for exactly canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}`, preserving all existing task-list API/dashboard/manual-navigation contracts. The implementation remains API-route-local and does not add dashboard/browser consumption, automatic traversal, search/sort/discovery, status+offset without limit, row traversal, replay/lifecycle mutation, services/MCP/dependency/CI/deployment changes, credentials, or production operations.

### Story 120.3 — Phase 41 / Epic 120 final validation closure

**Status:** future / not started.

**Intent:** Docs/status final closure with implementation commit, review/QA, and green CI evidence after Story 120.2, if implemented.

## Dependency and sequencing notes

1. Story 120.1 must complete planning consensus with Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR before any runtime/API/test implementation.
2. Story 120.2 must remain API-route-local and must not add dashboard/browser changes, status+offset without limit, automatic traversal, sorting, search/discovery, row traversal, URL/storage state, mutation/control behavior, services/MCP/dependency/CI/deployment changes, credentials, or production operations.
3. Story 120.3 may close only after implementation, final review, proportional QA, commit, and CI evidence exist.
