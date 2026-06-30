# Phase 45 Epics — Task List Sort Vocabulary Planning

Generated: 2026-06-30T19:35:36Z

## Phase 45 theme

Phase 45 opens the bounded API-local aggregate task-list sort vocabulary branch. The canonical vocabulary contract lives in `phase-45-architecture-amendment.md`; this file sequences delivery against that source.

- Family: read-only aggregate task-list API-local finite sort vocabulary expansion
- Exact future route: `GET /v1/tasks?sort={task_sort}`
- Existing token: `updated_at_desc_id_asc`
- New selected token: `created_at_desc_id_asc`
- Sort selector: one raw ASCII `sort` query key only
- Scope: docs/status-only planning first; no API/runtime/test/browser implementation until Architect then Critic consensus

## Epic 124 — Task list sort vocabulary boundary

### Objective

Plan, prove, implement, and close a bounded API-local sort vocabulary expansion that adds exactly `created_at_desc_id_asc` to the existing singleton task-list sort route without arbitrary sort grammar, browser vocabulary changes, sort composition with status/limit/offset, search/discovery, hidden selectors, automatic traversal, replay/lifecycle mutation, broad dashboard wiring, generated live data, or production operations.

### Story 124.1 — Task list sort vocabulary planning

**Status:** done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus.

**Intent:** Create Phase 45 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and consensus evidence selecting exact API-local finite sort vocabulary expansion for `GET /v1/tasks?sort={task_sort}` as the future runtime candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 45 PRD amendment exists and selects read-only aggregate task-list API-local finite sort vocabulary planning.
2. Phase 45 architecture amendment defines exact raw selector source, two-token route vocabulary, deterministic order branches, response metadata validation, no sort composition, and deferred surfaces.
3. Phase 45 epics file exists and sequences planning before implementation and final closure.
4. Story 124.1 artifact exists and records non-authorization, future test obligations, verification plan, consensus evidence, and completion evidence.
5. Sprint status opens Phase 45/Epic 124, marks Story 124.1 done only after Architect/Critic consensus, and keeps implementation future work.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim created-time sort implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 124.2 — Task list sort vocabulary API-local runtime boundary

**Status:** done; Story 124.2 implementation completed after Story 124.1 consensus.

**Intent:** Tests-first API-route-local implementation for exact finite vocabulary `updated_at_desc_id_asc | created_at_desc_id_asc`, strict raw query validation, deterministic ordering, selected_sort metadata, and preservation of all existing task-list API/dashboard/manual-navigation/singleton-sort contracts. Story 124.2 must not add browser controls, sort composition, arbitrary sort grammar, search/discovery, hidden selectors, automatic traversal, row traversal, replay/lifecycle mutation, services/MCP/dependency/CI/deployment changes, credentials, or production operations.

### Story 124.3 — Phase 45 / Epic 124 final validation closure

**Status:** done; Story 124.3 final closure completed after Story 124.2 review, QA, local validation, commit, and CI evidence.

**Intent:** Docs/status final closure with Story 124.2 runtime verification, review/QA, local validation, implementation commit, and remote CI evidence if implemented.

## Dependency and sequencing notes

1. Story 124.1 must complete planning consensus with Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR before any API/runtime/test implementation.
2. Story 124.2 must remain API-route-local and must not add browser/dashboard vocabulary changes, sort composition, search/discovery, arbitrary grammar, mutation/control behavior, services/MCP/dependency/CI/deployment changes, credentials, or production operations.
3. Story 124.3 closes only after implementation, final review, proportional QA, local validation, and remote CI evidence when available.


## Closure update — 2026-06-30

Story 124.1, Story 124.2, and Story 124.3 are done. Phase 45 / Epic 124 is closed by `124-3-phase-45-epic-124-final-closure.md` after implementation commit `dceae62f30cacd118b03ec08a8970b642d7ba333` and remote `ci` run `28476062586` (https://github.com/salacoste/oh-my-bmad/actions/runs/28476062586) passed.
