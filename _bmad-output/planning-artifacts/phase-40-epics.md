# Phase 40 Epics — Manual Task-List Pagination Navigation Planning

Generated: 2026-06-29T01:43:57Z

## Phase 40 theme

Phase 40 opens the bounded manual navigation branch for the already implemented and browser-consumed task-list limit+offset boundary:

- Family: read-only aggregate task-list manual pagination navigation planning
- Exact future candidate: visible manual previous-offset and next-offset controls inside the aggregate-task-list panel
- Underlying route: canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}` only
- Limit selector: existing visible ASCII integer control, 1 through 50 inclusive
- Offset selector: existing visible ASCII non-negative integer control, 0 through 2147483647 inclusive, raw spelling 1-10 ASCII digits
- Scope: docs/status-only planning first; no runtime/browser controls until Architect then Critic consensus

## Epic 119 — Manual task-list pagination navigation boundary

### Objective

Plan, prove, and eventually close a bounded dashboard/browser manual navigation boundary for adjacent aggregate task-list windows without automatic traversal, infinite scroll, sorting, free-text search, arbitrary discovery, hidden selectors, row-driven drill-down, URL/storage state, replay/lifecycle mutation, broad dashboard wiring, generated live data, or production operations.

### Story 119.1 — Manual task-list pagination navigation planning

**Status:** done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus.

**Intent:** Create Phase 40 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and consensus evidence that select visible manual previous-offset and next-offset controls inside the existing aggregate-task-list panel as the future candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 40 PRD amendment exists and selects read-only aggregate task-list manual pagination navigation planning.
2. Phase 40 architecture amendment defines exact route reuse, visible selector/provenance source, manual next/previous semantics, fail-closed edge states, and deferred surfaces.
3. Phase 40 epics file exists and sequences planning before implementation and final closure.
4. Story 119.1 artifact exists and records non-authorization, future test obligations, verification plan, consensus evidence, and completion evidence.
5. Sprint status opens Phase 40/Epic 119, marks Story 119.1 done only after Architect/Critic consensus, and keeps implementation future work.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim manual pagination navigation implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 119.2 — Manual task-list pagination navigation runtime boundary

**Status:** backlog / not started; selected as the future candidate by Story 119.1 consensus, but implementation remains deferred until a separately scoped Story 119.2 begins.

**Intent:** Tests-first dashboard aggregate-task-list browser/runtime manual previous/next navigation using exactly canonical `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`, from visible selector/provenance state only, preserving all existing task-list/dashboard contracts.

### Story 119.3 — Phase 40 / Epic 119 final validation closure

**Status:** future / not started.

**Intent:** Docs/status final closure with implementation commit, review/QA, and green CI evidence after Story 119.2, if implemented.

## Dependency and sequencing notes

1. Story 119.1 must complete planning consensus with Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR before any runtime/browser/test implementation.
2. Story 119.2 must remain dashboard-panel-local and must not add backend/API route changes, automatic traversal, status+offset/status+limit+offset composition, sorting, search/discovery, row traversal, URL/storage state, mutation/control behavior, services/MCP/dependency/CI/deployment changes, credentials, or production operations.
3. Story 119.3 may close only after implementation, final review, proportional QA, push, and remote CI evidence exist.
