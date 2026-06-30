# Phase 43 Epics — Task List Sort API-local Route Planning

Generated: 2026-06-29T20:58:06Z

## Phase 43 theme

Phase 43 opens the bounded API-local aggregate task-list finite sort branch:

- Family: read-only aggregate task-list API-local finite sort selection
- Exact future candidate: canonical `GET /v1/tasks?sort={task_sort}` only
- Sort selector: exactly one `sort` query key
- Approved sort vocabulary: `updated_at_desc_id_asc` only
- Sort semantics: `updated_at` descending, then `id` ascending deterministic tie-breaker
- Scope: docs/status-only planning first; no runtime/API/test implementation until Architect then Critic consensus

## Epic 122 — Task list sort API-local boundary

### Objective

Plan, prove, implement, and close a bounded API-local task-list sort route that makes the existing deterministic task-list ordering explicitly requestable through one finite sort token, without browser wiring, search/discovery, hidden selectors, automatic traversal, broader selector composition, replay/lifecycle mutation, broad dashboard wiring, generated live data, or production operations.

### Story 122.1 — Task list sort API-local planning

**Status:** pending sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus.

**Intent:** Create Phase 43 PRD, architecture, epics, story artifact, sprint-status opening, derivative feature-status refresh, and consensus evidence selecting exact canonical `GET /v1/tasks?sort={task_sort}` as the future API-local runtime candidate.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 43 PRD amendment exists and selects read-only aggregate task-list API-local finite sort planning.
2. Phase 43 architecture amendment defines exact route, singleton sort vocabulary, deterministic order semantics, response metadata, fail-closed selector states, and deferred surfaces.
3. Phase 43 epics file exists and sequences planning before implementation and final closure.
4. Story 122.1 artifact exists and records non-authorization, future test obligations, verification plan, consensus evidence, and completion evidence.
5. Sprint status opens Phase 43/Epic 122, marks Story 122.1 done only after Architect/Critic consensus, and keeps implementation future work.
6. `docs/feature-status.md` is refreshed as derivative status and does not claim task-list sort implementation.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 122.2 — Task list sort API-local runtime boundary

**Status:** done locally after tests-first implementation, code-review APPROVE/CLEAR, and UltraQA PASS.

**Intent:** Tests-first API-route-local implementation for exactly canonical `GET /v1/tasks?sort={task_sort}` with the singleton approved value `updated_at_desc_id_asc`, preserving all existing task-list API/dashboard/manual-navigation contracts. Story 122.2 completed locally with selected sort metadata, deterministic `updated_at DESC, id ASC`, fail-closed body/malformed/composed selector coverage, code-review APPROVE/CLEAR, and UltraQA PASS. The implementation remains API-local and does not add browser/dashboard controls, search/discovery, sort composition with status/limit/offset, automatic traversal, row traversal, replay/lifecycle mutation, services/MCP/dependency/CI/deployment changes, credentials, or production operations.

### Story 122.3 — Phase 43 / Epic 122 final validation closure

**Status:** backlog / not started.

**Intent:** Docs/status final closure with implementation commit, review/QA, and green CI evidence after Story 122.2, if implemented.

## Dependency and sequencing notes

1. Story 122.1 must complete planning consensus with Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR before any runtime/API/test implementation.
2. Story 122.2 must remain API-route-local and must not add browser/dashboard changes, search/discovery, broader sort vocabulary, automatic traversal, sort composition with existing selectors, mutation/control behavior, services/MCP/dependency/CI/deployment changes, credentials, or production operations.
3. Story 122.3 may close only after implementation, final review, proportional QA, commit, and CI evidence exist.
