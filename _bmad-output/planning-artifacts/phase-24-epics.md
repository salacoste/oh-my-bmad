# Phase 24 Epics — Event Timeline / Transitions Live-Read Route Selection

## Phase 24 theme

Phase 24 continues the narrow dashboard live-read route-family sequence. It opens the **Event timeline / transitions** branch as planning first, with runtime work deferred to a separate story and final closure deferred until review and CI evidence exist.

Selected future route family:

- `GET /v1/tasks/{task_id}/events`
- `GET /v1/tasks/{task_id}/transitions`

Non-selected route families remain future-only:

- trace correlation;
- history / replay;
- lifecycle readiness;
- task-list/search/discovery;
- aggregate/session/digest;
- generated live data;
- mutation/control surfaces.

## Epic 103 — Event timeline / transitions live-read runtime boundary

### Objective

Plan and later prove a task-scoped dashboard live-read boundary for event timeline and transition visibility without broad dashboard live wiring, discovery, aggregate/session/digest, or mutation/control behavior.

### Story 103.1 — Event timeline / transitions live-read route selection

**Status:** done by this planning/opening pass.

**Intent:** Create Phase 24 PRD, architecture, epics, story artifact, and sprint-status opening that select exactly the Event timeline / transitions route family.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 24 PRD amendment exists and selects exactly `GET /v1/tasks/{task_id}/events` and `GET /v1/tasks/{task_id}/transitions`.
2. Phase 24 architecture amendment exists and defines exact route, method, task_id, module, no-hidden-write, and deferred-surface boundaries.
3. Phase 24 epics file exists and sequences route selection before runtime boundary implementation and final closure.
4. Story 103.1 artifact exists and records non-authorization, future test obligations, verification plan, and completion evidence.
5. Sprint status sets `current_phase: 24`, keeps Epic 102 done, records Epic 102 retrospective done, opens Epic 103, and marks Story 103.1 done.
6. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 103.2 — Event timeline / transitions runtime boundary

**Status:** backlog.

**Intent:** Implement a separately approved, tests-first browser/runtime boundary for exactly the selected route family.

**Future acceptance criteria:**

1. Tests prove only `/v1/tasks/{task_id}/events` and `/v1/tasks/{task_id}/transitions` are reachable for this slice.
2. Calls are GET-only and body-free.
3. Visible task_id is the sole identifier source.
4. Event identifiers are display metadata only, not hidden route selectors.
5. Empty, stale, invalid, unauthorized, non-2xx, backend-unavailable, and network-failure states render bounded copy.
6. Source routes, task_id, freshness, authority, row-count/empty-state, and degraded-state metadata are visible.
7. Existing health and task-detail runtime-boundary tests remain green.
8. No aggregate/session/digest, trace, history/replay, lifecycle readiness, task-list/search/discovery, generated live data, or control behavior is introduced.
9. Independent code-reviewer APPROVE and architect CLEAR are recorded.
10. Remote CI is green before runtime completion is claimed.

### Story 103.3 — Phase 24 / Epic 103 final validation closure

**Status:** backlog.

**Intent:** Complete docs/status final closure only after Story 103.2 runtime evidence, final review, QA decision, push, and remote CI evidence exist.

**Future acceptance criteria:**

1. Closure artifact names exact implemented route family, changed files, review lanes, QA decision, commit(s), and CI run.
2. Closure wording does not imply broad dashboard live wiring.
3. Sprint status marks Epic 103 done only after all Epic 103 stories are done.
4. Deferred surfaces remain explicit and fail-closed.
5. Final docs/status verification and `git diff --check` pass.

## Dependency and sequencing notes

1. Story 103.1 must complete before any runtime code is authorized.
2. Story 103.2 must remain task-scoped and cannot add discovery.
3. Story 103.3 must not run until implementation, final review, QA decision, push, and remote CI evidence exist.
4. Trace correlation remains a separate future candidate after event/transition scope is closed or explicitly deferred.
5. Aggregate/session/digest remains higher-risk and fail-closed unless selected by a later explicit product and architecture gate.
