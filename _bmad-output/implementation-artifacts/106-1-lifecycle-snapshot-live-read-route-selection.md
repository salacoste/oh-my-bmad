# Story 106.1 — Lifecycle / Snapshot Live-Read Route Selection

## Status

Done — docs/status-only planning opening.

## Read surface selected

- `GET /v1/events/replay/snapshots`
- passive lifecycle-readiness evidence display from `dashboard/static/replay-lifecycle-contract.json`

## Non-authorization statement

This story does not authorize runtime implementation, dashboard JavaScript/HTML behavior changes, backend/API expansion, tests, dependencies, CI/deployment changes, services, MCP changes, generated live data, `POST /v1/events/replay/snapshots`, snapshot creation, snapshot deletion, lifecycle apply/prune/rollback, archive/manifest mutation, replay execution, background jobs, task-list/search/discovery, aggregate/session/digest, or mutation/control behavior.

## Planning evidence

- Context snapshot: `.omx/context/1-check-planning-status-for-the-next-open-backlo-20260624T143410Z.md`
- Deep-interview handoff: `.omx/interviews/phase-27-lifecycle-snapshot-planning-deep-interview.md`
- RALPLAN: `.omx/specs/phase-27-lifecycle-snapshot-planning-ralplan.md`
- Test spec: `.omx/specs/phase-27-lifecycle-snapshot-planning-test-spec.md`
- Architect review: `.omx/specs/phase-27-lifecycle-snapshot-planning-architect-review.md` — APPROVE/CLEAR.
- Critic review: `.omx/specs/phase-27-lifecycle-snapshot-planning-critic-review.md` — APPROVE with WATCH evidence note resolved in this artifact.

## Acceptance criteria evidence

1. Phase 27 PRD amendment exists and selects exactly `GET /v1/events/replay/snapshots` plus passive lifecycle-readiness evidence display.
2. Phase 27 architecture amendment defines exact route, method/body rules, snapshot list semantics, passive evidence discipline, no-hidden-write, no-snapshot-create, no-lifecycle-mutation, and deferred-surface boundaries.
3. Phase 27 epics sequence Story 106.1 route selection, Story 106.2 runtime boundary, and Story 106.3 final closure.
4. Sprint status opens Phase 27 / Epic 106 and marks Story 106.1 done while preserving Epic 105 done.
5. Runtime implementation is deferred to Story 106.2 and must be tests-first.
6. `docs/feature-status.md` is refreshed as a derivative status summary and does not claim lifecycle/snapshot runtime implementation is complete.

## Future Story 106.2 obligations

- Exact route allowlist for `GET /v1/events/replay/snapshots` only.
- GET-only and body-free dashboard calls.
- No `POST /v1/events/replay/snapshots`, snapshot creation, snapshot deletion, snapshot mutation, or snapshot internals as generated live data.
- Passive lifecycle-readiness evidence as display/provenance metadata only.
- No hidden snapshot or lifecycle selector from query/hash/storage/polling/discovery.
- No lifecycle apply/prune/rollback, archive/manifest mutation, replay execution, task-list/search/discovery, aggregate/session/digest, generated live data, background jobs, cache warming, or controls.
- Degraded-state, freshness, authority, provenance, and no-hidden-write/import guards.
- Existing health, task-detail, event/transition, trace, and history/replay runtime-boundary tests remain green.
- Independent review, QA, push, and remote CI green before completion.

## Verification plan

- YAML parse for sprint-status.
- `git diff --check`.
- Changed-file gate including tracked and untracked files, proving only Story 106.1 docs/status and OMX evidence files changed.
- Grep checks for selected route, excluded POST/snapshot creation/lifecycle mutation surfaces, and fail-closed deferred surfaces.
