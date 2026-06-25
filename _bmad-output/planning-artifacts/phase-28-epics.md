# Phase 28 Epics — Snapshot Creation Authorization Planning

## Phase 28 theme

Phase 28 continues the narrow dashboard/operator route-family sequence. It opens the **Snapshot Creation authorization** branch as planning first, with runtime work deferred to a separate story and final closure deferred until review and CI evidence exist.

Selected future surface:

- `POST /v1/events/replay/snapshots`

Non-selected surfaces remain future-only and fail-closed:

- destructive lifecycle apply/prune/rollback;
- destructive lifecycle authorization execution and retention jobs;
- archive/manifest mutation and object-storage lifecycle jobs;
- snapshot deletion, snapshot restore, and snapshot internals browsing;
- task-list/search/discovery;
- aggregate/session/digest;
- generated live data and replay execution target selection;
- broad dashboard wiring;
- services/MCP/dependencies/CI/deployment changes;
- production credential-gated operations.

## Epic 107 — Snapshot creation authorization boundary

### Objective

Plan and later prove a bounded operator-authorized boundary for replay snapshot creation through `POST /v1/events/replay/snapshots` without destructive lifecycle work, archive/manifest mutation, broad dashboard wiring, discovery/search, aggregate/session/digest, generated live data, replay execution controls, or hidden/background writes.

### Story 107.1 — Snapshot creation authorization planning

**Status:** done by this planning/opening pass.

**Intent:** Create Phase 28 PRD, architecture, epics, story artifact, sprint-status opening, and OMX evidence that select exactly the Snapshot Creation authorization surface.

**Scope:** docs/status-only.

**Acceptance criteria:**

1. Phase 28 PRD amendment exists and selects exactly `POST /v1/events/replay/snapshots` as a future snapshot creation authorization surface.
2. Phase 28 architecture amendment exists and defines exact route, operator-initiation, authorization, no-hidden-write, bounded metadata, concurrency/idempotency, and deferred-surface boundaries.
3. Phase 28 epics file exists and sequences planning before runtime-boundary implementation and final closure.
4. Story 107.1 artifact exists and records non-authorization, future test obligations, verification plan, and completion evidence.
5. Sprint status sets `current_phase: 28`, keeps Epic 106 done, opens Epic 107, and marks Story 107.1 done.
6. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files change.

### Story 107.2 — Snapshot creation authorization runtime boundary

**Status:** backlog.

**Intent:** Implement a separately approved, tests-first authorization/runtime boundary for exactly the selected snapshot creation surface.

**Future acceptance criteria:**

1. Tests prove only `POST /v1/events/replay/snapshots` is reachable for snapshot creation in this slice.
2. Snapshot creation requires visible operator initiation and explicit authorization/confirmation before network invocation.
3. Story 107.2 pins one existing authorization source and does not add a new credential system, backend auth middleware, service token, capability tier, or production credential dependency; if no existing source is sufficient, the story is blocked.
4. Missing/invalid/stale/ambiguous/unauthorized authorization states fail closed before `POST`.
5. No page-load, polling, timers, storage/hash/query changes, background workers, websocket/xhr side channels, cache warming, automatic retries, or unrelated controls can create snapshots.
6. Duplicate-submit, in-flight, timeout, retry, and concurrent-creation behavior is bounded: one operator action creates at most one in-flight request, duplicate submits are locally blocked, failed/unknown outcomes do not auto-retry `POST`, and a second creation after success requires a fresh visible operator action.
7. Successful output displays bounded metadata only and does not expose snapshot internals as generated live data or hidden route selectors.
8. The current backend API success contract remains body-free `POST` returning HTTP `201` with snapshot metadata unless a later planning gate approves API redesign.
9. `GET /v1/events/replay/snapshots` listing and lifecycle passive evidence remain read-only by effect.
10. Destructive lifecycle apply/prune/rollback, archive/manifest mutation, snapshot deletion/restore, task-list/search/discovery, aggregate/session/digest, generated live data, replay execution target selection, broad dashboard wiring, services/MCP/dependencies/CI/deployment changes, controls, and production credential operations remain unreachable.
11. Existing health, task-detail, event/transition, trace, history/replay, and lifecycle/snapshot GET runtime-boundary tests remain green.
12. Independent code-reviewer APPROVE and architect CLEAR are recorded.
13. Remote CI is green before runtime completion is claimed.

### Story 107.3 — Phase 28 / Epic 107 final validation closure

**Status:** backlog.

**Intent:** Complete docs/status final closure only after Story 107.2 runtime evidence, final review, QA decision, push, and remote CI evidence exist.

**Future acceptance criteria:**

1. Closure artifact names exact implemented snapshot creation surface, changed files, review lanes, QA decision, commit(s), and CI run.
2. Closure wording does not imply destructive lifecycle, archive/manifest mutation, broad dashboard wiring, discovery/search, aggregate/session/digest, generated live data, replay execution target selection, services/MCP/dependencies/CI expansion, or production operations.
3. Sprint status marks Epic 107 done only after all Epic 107 stories are done.
4. Final docs/status verification and `git diff --check` pass.

## Dependency and sequencing notes

1. Story 107.1 must complete before any snapshot creation dashboard/operator runtime work is authorized.
2. Story 107.2 must remain snapshot-create scoped and cannot add lifecycle apply/prune/rollback, archive/manifest mutation, discovery/search, aggregate/session/digest, generated live data, replay execution controls, broad dashboard wiring, services/MCP/dependencies/CI/deployment changes, or production operations.
3. Story 107.3 must not run until implementation, final review, QA decision, push, and remote CI evidence exist.
4. Destructive lifecycle and broader dashboard surfaces remain higher-risk and fail-closed unless selected by a later explicit product and architecture gate.
