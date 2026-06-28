# Phase 38 Epics — Task List Pagination Route-Selection Planning

## Phase 38 theme

Phase 38 closes the bounded task-list pagination / next-window API branch after planning, tests-first implementation, review, QA, push, and green remote CI evidence:

- Family: read-only aggregate task-list pagination / next-window API planning
- Exact future candidate: canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`
- Limit selector: ASCII integer 1 through 50 inclusive
- Offset selector: ASCII non-negative integer from 0 through 2147483647 inclusive
- Scope: backend/API route-local boundary only; no browser pagination controls or traversal

## Epic 117 — Task list pagination boundary

### Objective

Plan, prove, and close a bounded API-local task-list pagination boundary without browser traversal, infinite scroll, sorting, free-text search, arbitrary discovery, hidden selectors, row-driven drill-down, replay/lifecycle mutation, broad dashboard wiring, generated live data, or production operations.

### Story 117.1 — Task list pagination route-selection planning

**Status:** done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus.

**Intent:** Create Phase 38 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and consensus evidence that select read-only aggregate task-list pagination / next-window API planning and exactly canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}` as the future candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 38 PRD amendment exists and selects read-only aggregate task-list pagination / next-window API planning.
2. Phase 38 architecture amendment defines exact route, selector domains, canonical query spelling/order, future metadata requirements, fail-closed states, and deferred surfaces.
3. Phase 38 epics file exists and sequences planning before implementation and final closure.
4. Story 117.1 artifact exists and records non-authorization, future test obligations, verification plan, consensus evidence, and completion evidence.
5. Sprint status opens Phase 38/Epic 117, marks Story 117.1 done after Architect/Critic consensus, and does not mark implementation complete.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim pagination implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 117.2 — Task list pagination runtime/API boundary

**Status:** done after tests-first implementation, code-review APPROVE/CLEAR, UltraQA PASS, push, CI repair, and green remote CI evidence.

**Intent:** Tests-first API-local implementation for exactly canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`.

### Story 117.3 — Phase 38 / Epic 117 final validation closure

**Status:** done after final commit/CI evidence was recorded.

**Intent:** Docs/status final closure with implementation commit, CI repair/final head, GitHub Actions `ci` run `28339322034` success, and nightly run `28339322019` success.

## Dependency and sequencing notes

1. Story 117.1 completed planning consensus with Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR before any runtime/API/test implementation.
2. Story 117.2 remained API-local and did not add browser pagination controls, sort/search/discovery, status+offset/status+limit+offset composition, automatic row traversal, replay/lifecycle mutation, services/MCP/dependency/CI/deployment changes, or production operations.
3. Story 117.3 completed after implementation, final review, proportional QA, push, CI repair, and remote CI/nightly evidence existed.

Generated: 2026-06-28T19:13:22Z
