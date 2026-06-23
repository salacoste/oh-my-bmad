# Story 105.1 — History / Replay Live-Read Route Selection

## Status

Done — docs/status-only planning opening.

## Route family selected

- `GET /v1/tasks/{task_id}/history`
- `GET /v1/events/replay`
- `GET /v1/events/replay/validate`

## Non-authorization statement

This story does not authorize runtime implementation, dashboard JavaScript/HTML behavior changes, backend/API expansion, tests, dependencies, CI/deployment changes, services, MCP changes, generated live data, replay execution, snapshot creation, archive/manifest mutation, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, or mutation/control behavior.

## Planning evidence

- Context snapshot: `.omx/context/phase-26-next-dashboard-live-read-route-20260623T125457Z.md`
- Deep-interview handoff: `.omx/interviews/phase-26-next-dashboard-live-read-route-deep-interview.md`
- RALPLAN: `.omx/specs/phase-26-history-replay-planning-ralplan.md`
- Test spec: `.omx/specs/phase-26-history-replay-planning-test-spec.md`
- Architect review: `.omx/specs/phase-26-history-replay-planning-architect-review.md` — APPROVE/CLEAR after replay target and bounded rendering amendments.
- Critic review: `.omx/specs/phase-26-history-replay-planning-critic-review.md` — APPROVE after untracked-file changed-file gate amendment.

## Acceptance criteria evidence

1. Phase 26 PRD amendment exists and selects exactly the three history/replay routes.
2. Phase 26 architecture amendment defines exact routes, replay target query discipline, bounded rendering, no-hidden-write, no-lifecycle, and deferred-surface boundaries.
3. Phase 26 epics sequence Story 105.1 route selection, Story 105.2 runtime boundary, and Story 105.3 final closure.
4. Sprint status opens Phase 26 / Epic 105 and marks Story 105.1 done while preserving Epic 104 done.
5. Runtime implementation is deferred to Story 105.2 and must be tests-first.

## Future Story 105.2 obligations

- Exact route allowlist for `GET /v1/tasks/{task_id}/history`, `GET /v1/events/replay`, and `GET /v1/events/replay/validate` only.
- GET-only and body-free dashboard calls.
- Visible `task_id` selector for task history.
- Visible replay target query for `/v1/events/replay`: exactly one explicit, visible `to_sequence` or `to_timestamp` replay target query.
- No hidden defaults, URL query/hash scraping, storage, polling, or discovery-derived replay target.
- No raw replay materialized `state`, task/session rows, or validation diff values rendered as aggregate/session/search/discovery output.
- No `/v1/events/replay/snapshots`, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, replay execution, snapshot creation, archive/manifest mutation, or mutation/control reachability.
- Degraded-state, freshness, authority, provenance, and no-hidden-write/import guards.
- Independent review, QA, push, and remote CI green before completion.

## Verification plan

- YAML parse for sprint-status.
- `git diff --check`.
- Changed-file gate including tracked and untracked files, proving only the five Story 105.1 docs/status files changed.
- Grep checks for selected routes and fail-closed deferred surfaces.
