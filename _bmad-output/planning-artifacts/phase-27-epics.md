# Phase 27 Epics — Lifecycle / Snapshot Live-Read Route Selection

## Phase 27 theme

Phase 27 continues the narrow dashboard live-read route-family sequence. It opens the **Lifecycle / Snapshot** branch as planning first, with runtime work deferred to a separate story and final closure deferred until review and CI evidence exist.

Selected future read surface:

- `GET /v1/events/replay/snapshots`
- passive lifecycle-readiness evidence display from `dashboard/static/replay-lifecycle-contract.json`

Non-selected route families and surfaces remain future-only and fail-closed:

- `POST /v1/events/replay/snapshots` and snapshot creation;
- lifecycle apply/prune/rollback, destructive lifecycle authorization execution, archive/manifest mutation, scheduled retention, and object-storage lifecycle jobs;
- task-list/search/discovery;
- aggregate/session/digest;
- generated live data;
- replay execution and background jobs;
- mutation/control surfaces.

## Epic 106 — Lifecycle / Snapshot live-read runtime boundary

### Objective

Plan and later prove a bounded dashboard live-read boundary for replay snapshot listing and passive lifecycle-readiness evidence visibility without broad dashboard live wiring, snapshot creation, lifecycle mutation, replay execution, discovery, aggregate/session/digest, generated live data, or mutation/control behavior.

### Story 106.1 — Lifecycle / Snapshot live-read route selection

**Status:** done by this planning/opening pass.

**Intent:** Create Phase 27 PRD, architecture, epics, story artifact, derivative feature-status refresh, and sprint-status opening that select exactly the Lifecycle / Snapshot read surface.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 27 PRD amendment exists and selects exactly `GET /v1/events/replay/snapshots` plus passive lifecycle-readiness evidence display.
2. Phase 27 architecture amendment exists and defines exact route, method/body rules, snapshot list semantics, passive evidence discipline, no-hidden-write, no-snapshot-create, no-lifecycle-mutation, and deferred-surface boundaries.
3. Phase 27 epics file exists and sequences route selection before runtime boundary implementation and final closure.
4. Story 106.1 artifact exists and records non-authorization, future test obligations, verification plan, and completion evidence.
5. Sprint status sets `current_phase: 27`, keeps Epic 105 done, opens Epic 106, and marks Story 106.1 done.
6. `docs/feature-status.md` is refreshed as a derivative summary without claiming runtime implementation for lifecycle/snapshot.
7. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 106.2 — Lifecycle / Snapshot runtime boundary

**Status:** backlog.

**Intent:** Implement a separately approved, tests-first browser/runtime boundary for exactly the selected read surface.

**Future acceptance criteria:**

1. Tests prove only `GET /v1/events/replay/snapshots` is reachable for this slice.
2. Calls are GET-only and body-free.
3. Snapshot list entries are display/provenance metadata only and cannot drive hidden replay, archive traversal, discovery, or controls.
4. Passive lifecycle-readiness evidence fields are displayed as bounded metadata only and cannot trigger lifecycle apply/prune/rollback, snapshot creation, archive/manifest mutation, background jobs, generated live data, or production operations.
5. Empty/unavailable, stale, invalid, unauthorized, non-2xx, backend-unavailable, network-failure, missing evidence, failed replay validation, stale replay evidence, missing rollback evidence, and invalid archive configuration states render bounded non-authoritative copy.
6. Source route, evidence source, freshness, authority, provenance, and degraded-state metadata are visible.
7. `POST /v1/events/replay/snapshots`, lifecycle apply/prune/rollback, archive/manifest mutation, task-list/search/discovery, aggregate/session/digest, generated live data, replay execution, background jobs, and controls remain unreachable.
8. Existing health, task-detail, event/transition, trace, and history/replay runtime-boundary tests remain green.
9. Independent code-reviewer APPROVE and architect CLEAR are recorded.
10. Remote CI is green before runtime completion is claimed.

### Story 106.3 — Phase 27 / Epic 106 final validation closure

**Status:** backlog.

**Intent:** Complete docs/status final closure only after Story 106.2 runtime evidence, final review, QA decision, push, and remote CI evidence exist.

**Future acceptance criteria:**

1. Closure artifact names exact implemented read surface, changed files, review lanes, QA decision, commit(s), and CI run.
2. Closure wording does not imply snapshot creation, lifecycle apply/prune/rollback, archive/manifest mutation, task-list/search/discovery, aggregate/session/digest, generated live data, replay execution, or controls.
3. Sprint status marks Epic 106 done only after all Epic 106 stories are done.
4. Final docs/status verification and `git diff --check` pass.

## Dependency and sequencing notes

1. Story 106.1 must complete before any lifecycle/snapshot runtime code is authorized.
2. Story 106.2 must remain lifecycle/snapshot-read scoped and cannot add snapshot creation, destructive lifecycle work, discovery, aggregate/session/digest, generated live data, replay execution, background jobs, or controls.
3. Story 106.3 must not run until implementation, final review, QA decision, push, and remote CI evidence exist.
4. Task-list/search/discovery and aggregate/session/digest remain higher-risk and fail-closed unless selected by a later explicit product and architecture gate.
