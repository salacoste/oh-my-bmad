# Phase 27 PRD Amendment — Lifecycle / Snapshot Live-Read Route Selection

## Summary

Phase 27 opens the next narrow dashboard live-read planning branch after Phase 26 closed History / Replay. Phase 27 selects exactly the **Lifecycle / Snapshot** read family for future runtime consideration:

- `GET /v1/events/replay/snapshots`
- passive lifecycle-readiness evidence display from `dashboard/static/replay-lifecycle-contract.json`

Story 106.1 is docs/status-only. It does not add runtime behavior, dashboard JavaScript, browser network calls, backend/API routes, tests, dependencies, CI/deployment changes, services, MCP changes, generated live data, snapshot creation, replay execution, lifecycle apply/prune/rollback, archive/manifest mutation, discovery, aggregation, or mutation/control behavior.

## Problem

The dashboard now has proven narrow live-read runtime boundaries for health/readiness, task detail, event timeline/transitions, trace correlation, and history/replay. The next deferred route family is lifecycle readiness and `/v1/events/replay/snapshots`, but that surface is especially easy to confuse with snapshot creation or destructive lifecycle operations. Phase 27 therefore starts with a planning gate that selects only snapshot listing and passive lifecycle-readiness evidence display.

## Goals

- Open Phase 27 / Epic 106 as the next post-History/Replay live-read planning branch.
- Select exactly `GET /v1/events/replay/snapshots` plus passive lifecycle-readiness evidence display.
- Keep Story 106.1 docs/status-only.
- Require a later tests-first runtime story before any browser/runtime wiring.
- Require future runtime code to prove GET-only snapshot listing, metadata-only lifecycle evidence, freshness/authority/provenance visibility, no-hidden-write behavior, and bounded degraded-state handling.

## Out of scope for Story 106.1

- Runtime implementation or dashboard JavaScript/HTML behavior changes.
- Backend/API route expansion or server contract changes.
- Test-code changes.
- `POST /v1/events/replay/snapshots` and snapshot creation.
- Lifecycle apply/prune/rollback, destructive lifecycle authorization, archive/manifest mutation, scheduled retention, object-storage lifecycle jobs, replay execution, background validation jobs, or cache-warming writes.
- Raw task/session/aggregate state, task-list/search/discovery, aggregate/session/digest, generated live data, trace/history/replay expansion beyond the selected snapshot list, mutation/control behavior, dependencies, lockfiles, deployment, CI, service, MCP, or generated-data changes.

## Functional requirements

- **FR230 — Phase 27 lifecycle/snapshot scope.** The repository records Phase 27 as the product-scope gate for the next narrow dashboard live-read route family after History / Replay.
- **FR231 — Exact route-family selection.** Story 106.1 selects exactly `GET /v1/events/replay/snapshots` and passive lifecycle-readiness evidence display from `dashboard/static/replay-lifecycle-contract.json`.
- **FR232 — Separate implementation story.** Runtime wiring for the selected route family requires a later separately approved story.
- **FR233 — Snapshot list only.** Future runtime work may list replay snapshots but must not create snapshots, load snapshot internals as generated live data, mutate snapshot storage, or call `POST /v1/events/replay/snapshots`.
- **FR234 — Passive lifecycle evidence only.** Future lifecycle-readiness display may show bounded evidence metadata such as plan hash, dry-run artifact reference, safety policy version, replay validation reference, rollback evidence reference, authorization reference, and archive manifest validation. It must not execute lifecycle apply/prune/rollback or treat evidence fields as controls.
- **FR235 — No behavior change in Story 106.1.** Story 106.1 must not change runtime/package/API/MCP/service/script/dashboard/test/deployment/dependency/lockfile/CI behavior.

## Non-functional requirements

- **NFR-S41 — Lifecycle/snapshot fail-closed safety.** Missing, stale, invalid, unauthorized, unavailable, empty, non-2xx, backend-unavailable, network-failure, unverifiable lifecycle evidence, failed replay validation, missing rollback evidence, or invalid archive configuration states render bounded non-authoritative or explicit unavailable copy in future runtime work.
- **NFR-S42 — Snapshot read-only-by-effect enforcement.** Future runtime work cannot import/call snapshot creation, lifecycle apply/prune/rollback, writer, archive/manifest mutation, replay execution, idempotency write, cache-warming, background job, discovery, aggregation, session traversal, or control helpers.
- **NFR-O31 — Lifecycle/snapshot auditability.** Future displayed values must be traceable to source route or passive evidence field, visible selector if any, freshness/retrieved-at or emitted-at, authority, provenance, and returned metadata.
- **NFR-M27 — Tests-first maintainability.** Future runtime implementation must add boundary tests before or with any runtime wiring.

## Acceptance criteria

1. Phase 27 PRD, architecture, and epics artifacts exist and define lifecycle/snapshot live-read route-selection scope.
2. Story 106.1 artifact records route-selection evidence, exact route selection, non-goals, future test obligations, verification plan, and completion criteria.
3. Sprint status sets `current_phase: 27`, opens Epic 106 / Story 106.1, preserves Epic 105 done, and records newest-first audit evidence.
4. Story 106.1 explicitly excludes runtime implementation, broad dashboard live wiring, backend/API expansion, `POST /v1/events/replay/snapshots`, snapshot creation, lifecycle apply/prune/rollback, archive/manifest mutation, task-list/search/discovery, aggregate/session/digest, generated live data, replay execution, mutation/control affordances, dependency/lockfile/CI/deployment changes, and test/runtime code changes.
5. Follow-on Phase 27 epics sequence docs/status opening first, lifecycle/snapshot runtime-boundary tests/implementation second, final closure third.
