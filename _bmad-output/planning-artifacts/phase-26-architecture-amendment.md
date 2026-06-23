# Phase 26 Architecture Amendment — History / Replay Live-Read Route Selection

## Decision summary

Phase 26 may proceed from the completed Trace correlation runtime-boundary proof into **History / Replay live-read route-family planning**. This amendment selects exactly these future routes:

- `GET /v1/tasks/{task_id}/history`
- `GET /v1/events/replay`
- `GET /v1/events/replay/validate`

Story 105.1 is docs/status-only. It does not authorize runtime implementation, browser network calls, backend/API expansion, test-code changes, dependencies, CI/deployment changes, services, MCP changes, generated live data, replay execution, lifecycle readiness, snapshots, or mutation/control surfaces.

## Inputs

- `_bmad-output/implementation-artifacts/104-3-phase-25-epic-104-final-closure.md`
- `_bmad-output/planning-artifacts/phase-25-epics.md`
- `dashboard/live_read_adapter.py`
- `dashboard/static/index.html`
- `tests/dashboard/test_live_read_lifecycle_panel_contracts.py`
- `tests/dashboard/test_static_shell.py`
- `.omx/specs/phase-26-history-replay-planning-ralplan.md`
- `.omx/specs/phase-26-history-replay-planning-architect-review.md`
- `.omx/specs/phase-26-history-replay-planning-critic-review.md`

## Route selection rationale

History/replay is the safest next route family because it follows Trace correlation and existing event provenance, is narrower than task-list/search/discovery and aggregate/session/digest, and already has inert/static dashboard contract metadata. It is still risky enough to require a planning-first split because replay routes can accidentally become replay execution, archive traversal, aggregate/session discovery, or lifecycle/snapshot surfaces.

## Architectural boundaries

### Boundary 1 — Story 105.1 is docs/status-only

Story 105.1 may create or update only Phase 26 planning artifacts, the Story 105.1 artifact, and sprint status. It must not edit runtime code, dashboard HTML/JS, tests, API/backend code, CI, dependencies, lockfiles, scripts, deployment files, package manifests, services, MCP servers, or generated live data.

### Boundary 2 — Exact route family only

Future Phase 26 runtime work may target only:

- `GET /v1/tasks/{task_id}/history`
- `GET /v1/events/replay`
- `GET /v1/events/replay/validate`

It may not silently include `/v1/events/replay/snapshots`, lifecycle readiness, task-list/search/discovery, aggregate overview, session list, digest, stream, generated live data, trace search/list/discovery, or control routes.

### Boundary 3 — Explicit replay target semantics

Future `/v1/events/replay` dashboard calls must use exactly one explicit, visible `to_sequence` or `to_timestamp` replay target query. Hidden defaults, URL query/hash scraping, storage-derived values, polling, event/task/session discovery, aggregate synthesis, or background target selection are forbidden.

### Boundary 4 — Bounded rendering, no aggregate/session discovery

Future history/replay runtime code may render bounded replay status, counts, provenance, freshness, authority, and degraded-state metadata. It must not render raw replay materialized `state`, task/session rows, or validation diff values as aggregate/session/search/discovery output unless a later explicit product and architecture gate selects that route family.

### Boundary 5 — Lifecycle and snapshots remain separate

`/v1/events/replay/snapshots`, snapshot creation, archive/manifest mutation, lifecycle apply/prune/rollback, destructive lifecycle authorization evidence, and lifecycle readiness remain separate future-only surfaces.

### Boundary 6 — No hidden writes or side effects

Future implementation tests must fail on writer imports/calls, lifecycle helper imports, snapshot creation, replay execution jobs, background validation jobs, idempotency writes, cache-warming write paths, archive mutation, manifest mutation, side-effectful reads, mutation/control vocabulary, or POST/PUT/PATCH/DELETE dashboard calls.

## Required future test strategy

A later runtime story must add tests before or with implementation that prove:

1. exact route allowlist for the three selected history/replay routes only;
2. GET-only and body-free dashboard calls;
3. visible `task_id` source for task history;
4. visible `to_sequence` xor `to_timestamp` target for `/v1/events/replay`;
5. no hidden default/query/hash/storage/polling/discovery replay target;
6. metadata-only adjacent identifiers;
7. no raw replay `state`, task/session rows, or validation diff values rendered as aggregate/session/search/discovery output;
8. no lifecycle/snapshot/control route reachability;
9. empty/unavailable/partial/stale/invalid/unauthorized/non-2xx/backend-unavailable/network-failure rendering semantics;
10. visible source route, selector/target, freshness/retrieved-at or emitted-at, authority, provenance identifiers, and degraded-state metadata;
11. static no-hidden-write/import grep guards;
12. existing health, task-detail, event/transition, and trace runtime-boundary tests remain green.

## Review requirements

Future runtime completion requires independent code-reviewer APPROVE, architect CLEAR, proportional QA, push, and remote CI green. Story 105.1 may skip UltraQA only if changed-file verification proves docs/status-only and code-review is clean.
