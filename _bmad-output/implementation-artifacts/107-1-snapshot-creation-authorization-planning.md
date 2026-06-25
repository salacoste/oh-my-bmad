# Story 107.1 — Snapshot Creation Authorization Planning

## Status

Done — docs/status-only planning opening.

## Candidate surface selected

- `POST /v1/events/replay/snapshots`

## Non-authorization statement

This story does not authorize runtime implementation, dashboard JavaScript/HTML behavior changes, browser network calls, backend/API changes, tests, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, destructive lifecycle apply/prune/rollback, destructive lifecycle authorization execution, archive/manifest mutation, snapshot deletion, snapshot restore, replay execution target selection, task-list/search/discovery, aggregate/session/digest, broad dashboard wiring, hidden/background writes, controls beyond the future single selected snapshot-create affordance, production credentials, or production operations.

## Planning evidence

- Context snapshot: `.omx/context/phase-28-snapshot-creation-planning-20260625T163130Z.md`
- Deep-interview handoff: `.omx/interviews/phase-28-snapshot-creation-planning-deep-interview.md`
- RALPLAN: `.omx/specs/phase-28-snapshot-creation-planning-ralplan.md`
- Test spec: `.omx/specs/phase-28-snapshot-creation-planning-test-spec.md`
- Architect review: `.omx/specs/phase-28-snapshot-creation-planning-architect-review.md` — APPROVE/CLEAR.
- Critic review: `.omx/specs/phase-28-snapshot-creation-planning-critic-review.md` — APPROVE.

## Acceptance criteria evidence

1. Phase 28 PRD amendment exists and selects exactly `POST /v1/events/replay/snapshots` as the future snapshot creation authorization surface.
2. Phase 28 architecture amendment defines exact route, operator-initiation, authorization, no-hidden-write, bounded metadata, concurrency/idempotency, and deferred-surface boundaries.
3. Phase 28 epics sequence Story 107.1 planning, Story 107.2 snapshot creation authorization runtime boundary, and Story 107.3 final closure.
4. Sprint status opens Phase 28 / Epic 107 and marks Story 107.1 done while preserving Epic 106 done.
5. Runtime implementation is deferred to Story 107.2 and must be tests-first.
6. No runtime/source/test/backend/API/dependency/CI/deployment/service/MCP/generated-data files changed in this planning story.

## Future Story 107.2 obligations

- Exact route allowlist for `POST /v1/events/replay/snapshots` only.
- Visible operator initiation and explicit authorization/confirmation before network invocation.
- One existing authorization source must be pinned; no new credential system, backend auth middleware, service token, capability tier, or production credential dependency may be added. If no existing source is sufficient, Story 107.2 is blocked.
- Missing/invalid/stale/ambiguous/unauthorized states fail closed before `POST`.
- No page-load, polling, timers, storage/hash/query changes, background workers, websocket/xhr side channels, cache warming, automatic retries, or unrelated controls can create snapshots.
- Duplicate-submit, in-flight, timeout, retry, and concurrent-creation behavior is bounded: one operator action creates at most one in-flight request; duplicate submits are locally blocked; failed/timeout/unknown outcomes render non-authoritative state and must not auto-retry `POST`; a second creation after success requires a fresh visible operator action.
- Current backend API success contract remains body-free `POST` returning HTTP `201` with bounded snapshot metadata unless a later planning gate approves API redesign.
- Successful output displays bounded metadata only: snapshot id, sequence number, timestamp, size, request/correlation id where available, authority, provenance, and freshness/completed-at.
- No lifecycle apply/prune/rollback, archive/manifest mutation, snapshot deletion/restore, task-list/search/discovery, aggregate/session/digest, generated live data, replay execution target selection, broad dashboard wiring, services/MCP/dependencies/CI/deployment changes, controls, production credentials, or production operations.
- Existing health, task-detail, event/transition, trace, history/replay, and lifecycle/snapshot GET runtime-boundary tests remain green.
- Independent review, QA, push, and remote CI green before completion.

## Verification plan

- YAML parse for sprint-status.
- `git diff --check`.
- Changed-file gate including tracked and untracked files, proving only Story 107.1 docs/status and OMX evidence files changed.
- Grep checks for selected route, excluded destructive lifecycle/broad dashboard/discovery/search/aggregate/session/digest/services/MCP/dependencies/CI/runtime implementation surfaces, and fail-closed deferred surfaces.
