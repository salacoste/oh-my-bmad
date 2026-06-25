# Phase 28 Architecture Amendment — Snapshot Creation Authorization Planning

## Decision summary

Phase 28 may proceed from the completed Lifecycle / Snapshot read boundary into **Snapshot Creation authorization planning**. This amendment selects exactly this future write-by-effect surface:

- `POST /v1/events/replay/snapshots`

Story 107.1 is docs/status-only. It does not authorize runtime implementation, browser network calls, dashboard JavaScript/HTML behavior changes, backend/API changes, test-code changes, dependencies, CI/deployment changes, services, MCP changes, generated live data, destructive lifecycle operations, archive/manifest mutation, discovery/search, aggregate/session/digest, broad dashboard wiring, or production operations.

## Inputs

- `_bmad-output/implementation-artifacts/106-3-phase-27-epic-106-final-closure.md`
- `_bmad-output/planning-artifacts/phase-27-epics.md`
- `docs/api-contracts.md`
- `docs/operator-runbook.md`
- `services/registry-api/src/registry_api/routes/replay.py`
- `.omx/interviews/phase-28-snapshot-creation-planning-deep-interview.md`
- `.omx/specs/phase-28-snapshot-creation-planning-ralplan.md`
- `.omx/specs/phase-28-snapshot-creation-planning-test-spec.md`

## Route selection rationale

Snapshot creation is the next narrow candidate because Phase 27 completed snapshot listing and lifecycle evidence display while explicitly deferring creation. The backend route already exists, but dashboard/operator exposure is a different safety decision because `POST /v1/events/replay/snapshots` creates a persisted snapshot artifact. A planning-first split prevents accidental lifecycle mutation, broad dashboard expansion, hidden writes, or discovery/aggregation drift.

## Architectural boundaries

### Boundary 1 — Story 107.1 is docs/status-only

Story 107.1 may create or update only Phase 28 planning artifacts, the Story 107.1 artifact, sprint status, and OMX workflow evidence. It must not edit runtime code, dashboard HTML/JS behavior, tests, API/backend code, CI, dependencies, lockfiles, scripts, deployment files, package manifests, services, MCP servers, or generated live data.

### Boundary 2 — Exact selected surface only

Future Phase 28 runtime work may target only `POST /v1/events/replay/snapshots` as a snapshot creation affordance. It may not silently include snapshot deletion, snapshot restore, snapshot internals browsing, lifecycle apply/prune/rollback, archive/manifest mutation, replay execution target selection, task-list/search/discovery, aggregate overview, session list, digest, stream, generated live data, broad dashboard wiring, or control routes.

### Boundary 3 — Operator initiation and authorization

Future invocation must be visible, deliberate, and operator-initiated. The UI or caller must make the selected action, authorization state, source route, side effect, and result visible. Missing/invalid/stale/ambiguous authorization must fail closed before any `POST` call. Story 107.2 must pin one existing authorization source already available in the product/API/dashboard stack at that time, such as existing operator session/auth context, existing capability policy, or an already-approved confirmation pattern. It must not add a new credential system, backend authorization middleware, service token, capability tier, or production credential dependency. If no existing authorization source is sufficient, Story 107.2 must stop as blocked and return to planning rather than widening scope.

### Boundary 4 — No hidden writes or automatic creation

Future tests must fail on load-time creation, polling/timer-triggered creation, cache warming, background jobs, automatic retries that create multiple snapshots, hash/query/storage-driven writes, worker/websocket/xhr side channels, or invocation from unrelated dashboard controls. `GET /v1/events/replay/snapshots` remains listing only; listing must not create. The initial runtime contract is body-free `POST` preserving the current backend API shape: successful creation returns HTTP `201` and bounded snapshot metadata. Authorization-denied, unavailable, duplicate in-flight, timeout, network-failure, non-2xx, invalid-response, and unknown-result states must render fail-closed/non-authoritative copy without issuing another `POST` automatically.

### Boundary 5 — Bounded returned metadata

A successful future snapshot-create affordance may display bounded metadata returned by the route: snapshot id, sequence number, timestamp, size, request/correlation id if available, freshness/completed-at, actor/authority, and provenance. Snapshot files, internal materialized state, archive paths, or replay targets must not become generated live data, hidden selectors, download paths, or mutation targets without a later explicit gate. A duplicate creation after a completed successful request is a new snapshot-create operation and requires a fresh visible operator action; it is not an implicit retry.

### Boundary 6 — Destructive lifecycle remains separate

Lifecycle apply/prune/rollback, retention execution, object-storage lifecycle jobs, archive/manifest mutation, destructive lifecycle authorization, scheduled cleanup, and production operations remain separate future-only surfaces. Snapshot creation is not approval to prune logs, apply lifecycle plans, mutate archives, or roll back state.

## Required future test strategy

A later runtime story must add tests before or with implementation that prove:

1. exact route allowlist for `POST /v1/events/replay/snapshots` only for snapshot creation;
2. no automatic or hidden invocation on page load, polling, timers, storage/hash/query changes, background workers, cache warming, websocket/xhr side channels, or unrelated controls;
3. visible operator initiation and a single pinned existing authorization source are required before a `POST` call;
4. no new credential system, backend auth middleware, service token, capability tier, or production credential dependency is introduced;
5. missing/invalid/stale/ambiguous/unauthorized states fail closed before network invocation;
6. duplicate submit, in-flight, timeout, retry, and concurrent creation behavior is bounded and does not create accidental duplicate snapshots;
7. returned metadata is bounded and does not expose snapshot internals as generated live data;
8. the existing `201` success response shape is preserved unless a later planning gate approves API redesign;
9. `GET /v1/events/replay/snapshots` listing and passive lifecycle evidence remain read-only by effect;
10. no lifecycle apply/prune/rollback, archive/manifest mutation, snapshot deletion/restore, replay execution target selection, task-list/search/discovery, aggregate/session/digest, generated live data, broad dashboard wiring, services/MCP/dependencies/CI/deployment changes, or production credential operations are reachable;
11. existing health, task-detail, event/transition, trace, history/replay, and lifecycle/snapshot GET runtime-boundary tests remain green.

## Review requirements

Future runtime completion requires independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green. Story 107.1 may skip UltraQA only if changed-file verification proves docs/status-only and code-review is clean.
