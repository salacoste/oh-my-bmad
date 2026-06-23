# Phase 26 PRD Amendment — History / Replay Live-Read Route Selection

## Summary

Phase 26 opens the next narrow dashboard live-read planning branch after Phase 25 closed Trace correlation. Phase 26 selects exactly the **history/replay** read family for future runtime consideration:

- `GET /v1/tasks/{task_id}/history`
- `GET /v1/events/replay`
- `GET /v1/events/replay/validate`

Story 105.1 is docs/status-only. It does not add runtime behavior, dashboard JavaScript, browser network calls, backend/API routes, tests, dependencies, CI/deployment changes, services, MCP changes, generated live data, replay execution, snapshot creation, lifecycle readiness, discovery, aggregation, or mutation/control behavior.

## Problem

The dashboard now has proven narrow live-read runtime boundaries for health/readiness, task detail, event timeline/transitions, and trace correlation. Existing static/read-contract artifacts already name task history and replay validation routes, but those route names can drift into broader behavior: replay execution, archive traversal, raw materialized state rendering, task/session discovery, lifecycle snapshots, or generated aggregate views.

Phase 26 therefore starts with a planning gate that selects history/replay only and explicitly separates it from lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, and controls.

## Goals

- Open Phase 26 / Epic 105 as the next post-trace-correlation live-read planning branch.
- Select exactly the history/replay route family listed above.
- Keep Story 105.1 docs/status-only.
- Require a later tests-first runtime story before any browser/runtime wiring.
- Require future runtime code to prove visible selector discipline, read-only-by-effect behavior, bounded replay rendering, freshness/authority visibility, and degraded-state handling.

## Out of scope for Story 105.1

- Runtime implementation or dashboard JavaScript/HTML behavior changes.
- Backend/API route expansion or server contract changes.
- Test-code changes.
- `/v1/events/replay/snapshots` and lifecycle readiness.
- Replay execution jobs, background validation jobs, snapshot creation, archive/manifest mutation, lifecycle apply/prune/rollback, or cache-warming writes.
- Raw replay materialized `state`, task/session rows, or validation diff values as aggregate/session/search/discovery output.
- Task-list/search/discovery, aggregate/session/digest, generated live data, trace search/list/discovery, mutation/control behavior, dependencies, lockfiles, deployment, CI, service, MCP, or generated-data changes.

## Functional requirements

- **FR223 — Phase 26 history/replay scope.** The repository records Phase 26 as the product-scope gate for the next narrow dashboard live-read route family after Trace correlation.
- **FR224 — Exact route-family selection.** Story 105.1 selects exactly `GET /v1/tasks/{task_id}/history`, `GET /v1/events/replay`, and `GET /v1/events/replay/validate`.
- **FR225 — Separate implementation story.** Runtime wiring for the selected route family requires a later separately approved story.
- **FR226 — Replay target boundary.** Future `/v1/events/replay` runtime work must require exactly one explicit, visible `to_sequence` or `to_timestamp` replay target query, and must not use hidden defaults, URL hash/query scraping, local/session storage, polling, or discovery-derived targets.
- **FR227 — Bounded replay rendering.** Future runtime work must not render raw replay materialized `state`, task/session rows, or validation diff values as aggregate/session/search/discovery output unless a later route family explicitly authorizes that surface.
- **FR228 — No lifecycle or snapshot drift.** `/v1/events/replay/snapshots`, lifecycle readiness, snapshot creation, archive/manifest mutation, lifecycle apply/prune/rollback, and destructive lifecycle work remain separate future-only surfaces.
- **FR229 — No behavior change in Story 105.1.** Story 105.1 must not change runtime/package/API/MCP/service/script/dashboard/test/deployment/dependency/lockfile/CI behavior.

## Non-functional requirements

- **NFR-S39 — History/replay fail-closed safety.** Missing, stale, partial, invalid, unauthorized, unavailable, non-2xx, backend-unavailable, or network-failure states render bounded non-authoritative or explicit unavailable copy in future runtime work.
- **NFR-S40 — Replay read-only-by-effect enforcement.** Future history/replay runtime work cannot import/call write, lifecycle, snapshot, replay-execution, archive/manifest mutation, idempotency write, cache-warming, background job, discovery, aggregation, session traversal, or control helpers.
- **NFR-O30 — History/replay auditability.** Future displayed values must be traceable to source route, visible selector/target, freshness/retrieved-at or emitted-at, authority, and returned provenance metadata.
- **NFR-M26 — Tests-first maintainability.** Future runtime implementation must add boundary tests before or with any runtime wiring.

## Acceptance criteria

1. Phase 26 PRD, architecture, and epics artifacts exist and define history/replay live-read route-selection scope.
2. Story 105.1 artifact records lifecycle evidence, exact route selection, non-goals, future test obligations, verification plan, and completion criteria.
3. Sprint status sets `current_phase: 26`, opens Epic 105 / Story 105.1, preserves Epic 104 done, and records newest-first audit evidence.
4. Story 105.1 explicitly excludes runtime implementation, broad dashboard live wiring, backend/API expansion, lifecycle readiness, snapshots, task-list/search/discovery, aggregate/session/digest, generated live data, replay execution, mutation/control affordances, dependency/lockfile/CI/deployment changes, and test/runtime code changes.
5. Follow-on Phase 26 epics sequence docs/status opening first, history/replay runtime-boundary tests/implementation second, final closure third.
