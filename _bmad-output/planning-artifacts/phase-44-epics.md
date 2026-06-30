# Phase 44 Epics — Task List Sort Browser Controls Planning

Generated: 2026-06-30T02:20:23Z

## Phase 44 theme

Phase 44 closed the bounded browser/dashboard aggregate-task-list singleton sort branch:

- Family: read-only aggregate-task-list browser/dashboard singleton sort consumption
- Exact future candidate: visible controls for `GET /v1/tasks?sort=updated_at_desc_id_asc`
- Sort selector: visible aggregate-task-list control state only
- Approved sort vocabulary: `updated_at_desc_id_asc` only
- Route composition: none; sort is standalone for this increment
- Scope: docs/status-only planning first; no dashboard/browser/test implementation until Architect then Critic consensus

## Epic 123 — Task list sort browser controls boundary

### Objective

Plan, prove, implement, and close a bounded dashboard aggregate-task-list sort-control path that exposes the existing singleton API-local sort route through visible controls only, without broader sort vocabulary, status/limit/offset composition, search/discovery, hidden selectors, automatic traversal, replay/lifecycle mutation, broad dashboard wiring, generated live data, or production operations.

### Story 123.1 — Task list sort browser controls planning

**Status:** done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus.

**Intent:** Create Phase 44 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and consensus evidence selecting exact visible browser controls for `GET /v1/tasks?sort=updated_at_desc_id_asc` as the future dashboard aggregate-task-list runtime candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 44 PRD amendment exists and selects read-only aggregate-task-list browser/dashboard singleton sort-controls planning.
2. Phase 44 architecture amendment defines exact visible-control source, singleton route, response metadata validation, fail-closed selector states, no sort composition, and deferred surfaces.
3. Phase 44 epics file exists and sequences planning before implementation and final closure.
4. Story 123.1 artifact exists and records non-authorization, future test obligations, verification plan, consensus evidence, and completion evidence.
5. Sprint status opens Phase 44/Epic 123, marks Story 123.1 done only after Architect/Critic consensus, and keeps implementation future work.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim dashboard sort-control implementation.
7. `docs/api-contracts.md` reflects the already-implemented singleton API sort route and keeps browser composition/broader sort surfaces deferred.
8. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 123.2 — Task list sort browser controls runtime boundary

**Status:** done locally after tests-first dashboard implementation and local dashboard verification.

**Intent:** Tests-first dashboard/browser implementation for visible aggregate-task-list sort controls that issue exactly `GET /v1/tasks?sort=updated_at_desc_id_asc`, validate `selected_sort` and bounded response metadata, render sorted-read results in a separate singleton-sort subtree, render fail-closed states, and leave all existing status/limit/offset/manual-navigation state unchanged. Story 123.2 must not add backend/API behavior changes, broader sort vocabulary, sort composition with status/limit/offset, search/discovery, hidden selectors, automatic traversal, row traversal, replay/lifecycle mutation, services/MCP/dependency/CI/deployment changes, credentials, or production operations.

### Story 123.3 — Phase 44 / Epic 123 final validation closure

**Status:** done; closes Phase 44 / Epic 123 after Story 123.2 browser controls verification.

**Intent:** Docs/status final closure with Story 123.2 browser-control verification, review/QA, and local validation evidence. This local closeout does not claim remote CI or commit evidence.

## Dependency and sequencing notes

1. Story 123.1 must complete planning consensus with Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR before any dashboard/browser/test implementation.
2. Story 123.2 must remain browser/dashboard-local and must not add backend/API changes, search/discovery, broader sort vocabulary, automatic traversal, sort composition with existing selectors, mutation/control behavior, services/MCP/dependency/CI/deployment changes, credentials, or production operations.
3. Story 123.3 closes locally after implementation, final review, proportional QA, and local validation evidence; remote CI/commit evidence is not claimed by this closeout.
