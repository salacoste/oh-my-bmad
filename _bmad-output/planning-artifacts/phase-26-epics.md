# Phase 26 Epics — History / Replay Live-Read Route Selection

## Phase 26 theme

Phase 26 continues the narrow dashboard live-read route-family sequence. It opens the **History / Replay** branch as planning first, with runtime work deferred to a separate story and final closure deferred until review and CI evidence exist.

Selected future route family:

- `GET /v1/tasks/{task_id}/history`
- `GET /v1/events/replay`
- `GET /v1/events/replay/validate`

Non-selected route families remain future-only and fail-closed:

- lifecycle readiness and `/v1/events/replay/snapshots`;
- task-list/search/discovery;
- aggregate/session/digest;
- generated live data;
- replay execution, snapshot creation, archive/manifest mutation, and lifecycle controls;
- mutation/control surfaces.

## Epic 105 — History / Replay live-read runtime boundary

### Objective

Plan and later prove a bounded dashboard live-read boundary for task history and replay validation visibility without broad dashboard live wiring, lifecycle readiness, snapshots, task-list/search/discovery, aggregate/session/digest, generated live data, raw replay-state discovery, replay execution, or mutation/control behavior.

### Story 105.1 — History/replay live-read route selection

**Status:** done by this planning/opening pass.

**Intent:** Create Phase 26 PRD, architecture, epics, story artifact, and sprint-status opening that select exactly the History / Replay route family.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 26 PRD amendment exists and selects exactly `GET /v1/tasks/{task_id}/history`, `GET /v1/events/replay`, and `GET /v1/events/replay/validate`.
2. Phase 26 architecture amendment exists and defines exact routes, method/body rules, replay target query discipline, bounded rendering, no-hidden-write, no-lifecycle, and deferred-surface boundaries.
3. Phase 26 epics file exists and sequences route selection before runtime boundary implementation and final closure.
4. Story 105.1 artifact exists and records non-authorization, future test obligations, verification plan, and completion evidence.
5. Sprint status sets `current_phase: 26`, keeps Epic 104 done, opens Epic 105, and marks Story 105.1 done.
6. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 105.2 — History/replay runtime boundary

**Status:** future.

**Intent:** Implement a separately approved, tests-first browser/runtime boundary for exactly the selected route family.

**Future acceptance criteria:**

1. Tests prove only the selected history/replay routes are reachable for this slice.
2. Calls are GET-only and body-free.
3. Visible `task_id` is the sole task-history selector.
4. `/v1/events/replay` requires exactly one explicit, visible `to_sequence` or `to_timestamp` replay target query.
5. No hidden default/query/hash/storage/polling/discovery replay target exists.
6. Event/task/session/replay identifiers are display metadata only unless explicitly visible selectors are authorized.
7. Raw replay `state`, task/session rows, and validation diff values are not rendered as aggregate/session/search/discovery output.
8. Empty/unavailable, partial, stale, invalid, unauthorized, non-2xx, backend-unavailable, and network-failure states render bounded copy.
9. Source route, selector/target, freshness, authority, linked identifiers, and degraded-state metadata are visible.
10. `/v1/events/replay/snapshots`, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, replay execution, snapshot creation, archive/manifest mutation, and controls remain unreachable.
11. Existing health, task-detail, event/transition, and trace runtime-boundary tests remain green.
12. Independent code-reviewer APPROVE and architect CLEAR are recorded.
13. Remote CI is green before runtime completion is claimed.

### Story 105.3 — Phase 26 / Epic 105 final validation closure

**Status:** future.

**Intent:** Complete docs/status final closure only after Story 105.2 runtime evidence, final review, QA decision, push, and remote CI evidence exist.

**Future acceptance criteria:**

1. Closure artifact names exact implemented route family, changed files, review lanes, QA decision, commit(s), and CI run.
2. Closure wording does not imply lifecycle readiness, snapshots, task-list/search/discovery, aggregate/session/digest, generated live data, replay execution, archive/manifest mutation, or control approval.
3. Sprint status marks Epic 105 done only after all Epic 105 stories are done.
4. Final docs/status verification and `git diff --check` pass.

## Dependency and sequencing notes

1. Story 105.1 must complete before any history/replay runtime code is authorized.
2. Story 105.2 must remain history/replay-scoped and cannot add lifecycle readiness, snapshots, discovery, aggregate/session/digest, replay execution, or control behavior.
3. Story 105.3 must not run until implementation, final review, QA decision, push, and remote CI evidence exist.
