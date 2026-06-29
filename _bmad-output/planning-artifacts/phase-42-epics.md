# Phase 42 Epics — Task Status + Limit + Offset Browser Consumption Planning

Generated: 2026-06-29T17:07:16Z

## Phase 42 theme

Phase 42 opens the bounded dashboard aggregate-task-list browser-consumption branch for task-list reads:

- Family: read-only aggregate task-list dashboard/browser bounded selector composition
- Exact future candidate: canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}` only
- Status selector: visible aggregate-task-list control with existing finite lifecycle status vocabulary
- Limit selector: visible aggregate-task-list control with ASCII integer 1 through 50 inclusive
- Offset selector: visible aggregate-task-list control with ASCII non-negative integer 0 through 2147483647 inclusive, raw spelling 1-10 ASCII digits
- Scope: docs/status-only planning first; no runtime/dashboard/test implementation until Architect then Critic consensus

## Epic 121 — Task status + limit + offset browser boundary

### Objective

Plan, prove, implement, and close a bounded aggregate-task-list browser route composition that requests one explicit filtered window for one lifecycle status, one bounded limit, and one bounded offset using visible controls only, without automatic traversal, search/sort/discovery, hidden selectors, row-driven drill-down, replay/lifecycle mutation, broad wiring, generated live data, or production operations.

### Story 121.1 — Task status + limit + offset browser-consumption planning

**Status:** done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus.

**Intent:** Create Phase 42 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and consensus evidence selecting exact canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}` as the future aggregate-task-list browser runtime candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 42 PRD amendment exists and selects read-only aggregate task-list dashboard/browser status+limit+offset route-composition planning.
2. Phase 42 architecture amendment defines exact route, selector domains, visible-control provenance, canonical query order, response metadata, fail-closed selector/response states, and deferred surfaces.
3. Phase 42 epics file exists and sequences planning before implementation and final closure.
4. Story 121.1 artifact exists and records non-authorization, future test obligations, verification plan, consensus evidence, and completion evidence.
5. Sprint status opens Phase 42/Epic 121, marks Story 121.1 done only after Architect/Critic consensus, and keeps implementation future work.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim browser status+limit+offset implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 121.2 — Task status + limit + offset browser runtime boundary

**Status:** implementation authorized after Story 121.1 consensus; in progress / pending implementation, code-review, and UltraQA evidence.

**Intent:** Tests-first dashboard aggregate-task-list implementation for exactly canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}`, preserving all existing task-list API/dashboard/manual-navigation contracts. The implementation remains dashboard aggregate-task-list local and does not add backend/API changes, automatic traversal, search/sort/discovery, status+offset without limit, row traversal, replay/lifecycle mutation, services/MCP/dependency/CI/deployment changes, credentials, or production operations.

### Story 121.3 — Phase 42 / Epic 121 final validation closure

**Status:** future / not started.

**Intent:** Docs/status final closure with implementation commit, review/QA, and green CI evidence after Story 121.2, if implemented.

## Dependency and sequencing notes

1. Story 121.1 must complete planning consensus with Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR before any runtime/dashboard/test implementation.
2. Story 121.2 must remain dashboard aggregate-task-list local and must not add backend/API changes, automatic traversal, sorting, search/discovery, row traversal, URL-storage state, mutation/control behavior, services/MCP/dependency/CI/deployment changes, credentials, or production operations.
3. Story 121.3 may close only after implementation, final review, proportional QA, commit, and CI evidence exist.
