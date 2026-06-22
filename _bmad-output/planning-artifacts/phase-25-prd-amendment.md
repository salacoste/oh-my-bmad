# Phase 25 PRD Amendment — Trace Correlation Live-Read Route Selection

## Summary

Phase 25 opens the next dashboard live-read planning branch after Phase 24 closed the Event timeline / transitions runtime boundary. Phase 25 selects exactly one next future route family:

- Trace correlation:
  - `GET /v1/trace/{trace_id}`

This PRD amendment is a product planning artifact. Story 104.1 does not add runtime behavior, dashboard JavaScript, browser network calls, backend/API routes, tests, dependencies, CI/deployment changes, services, MCP changes, generated live data, or mutation/control surfaces.

## Problem

The dashboard now has narrow live-read runtime proofs for Health/readiness, Task detail, and Event timeline / transitions. Event and transition rows can expose `trace_id` as provenance metadata, but using that metadata without a phase-level product boundary risks accidentally introducing trace search/list, event/task discovery, replay/history traversal, lifecycle readiness, aggregate/session joins, generated digests, hidden writes, or control-plane behavior.

Phase 25 therefore starts with docs/status-only route-family selection and guardrail definition. It chooses the smallest trace-related read family: one GET route with one required explicit identifier, `trace_id`.

## Goals

- Open Phase 25 / Epic 104 as the next post-event/transition live-read planning branch.
- Select exactly `GET /v1/trace/{trace_id}` as the next future route family.
- Preserve read-only-by-effect semantics: no hidden writes, no side-effectful reads, no cache-warming writes, no background dispatch, no mutation/control behavior.
- Require a later implementation story to prove route/method/module/effect/selector boundaries before claiming runtime completion.
- Keep history/replay, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, and control surfaces out of this route-selection story.

## Scope

IN for Story 104.1:

- Phase 25 PRD amendment.
- Phase 25 architecture amendment.
- Phase 25 epics.
- Story 104.1 lifecycle artifact.
- Sprint-status update opening Phase 25 / Epic 104 and marking Story 104.1 done.
- Future implementation requirements for Trace correlation `GET /v1/trace/{trace_id}`.
- Phase 24 / Epic 103 retrospective artifact.

OUT for Story 104.1:

- Runtime implementation of `GET /v1/trace/{trace_id}`.
- Browser `fetch`, XHR, WebSocket, EventSource, polling, frontend scripts, hidden HTTP clients, or dashboard/static behavior changes.
- Backend/API route expansion or server contract changes.
- Test-code changes.
- Trace search/list/discovery, automatic trace traversal, history, replay, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, stream, or control contracts.
- `event_id`, `task_id`, or `session_id` as trace route inputs; those identifiers may be row/provenance metadata only unless a later story proves an explicit visible `trace_id` source.
- Mutation/control/destructive lifecycle affordances including approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled jobs, credentialed production operation, credential entry, token minting, public sharing, OAuth, external hosting, or multi-user auth.
- Dependencies, lockfiles, deployment, CI, package, service, MCP, runtime framework, or generated-data changes.

## Functional requirements

- **FR215 — Phase 25 trace-correlation scope.** The repository records Phase 25 as the product-scope gate for the next narrow dashboard live-read route family after Event timeline / transitions.
- **FR216 — Exact route-family selection.** Story 104.1 selects exactly `GET /v1/trace/{trace_id}` and selects no other live-read route family.
- **FR217 — Separate implementation story.** Runtime wiring for the selected route family requires a later separately approved story, expected as Story 104.2 or equivalent.
- **FR218 — Trace identifier boundary.** Future implementation must require an explicit visible `trace_id` source and must not introduce trace search/list, hidden discovery, task-list/search, event lookup, session traversal, aggregate synthesis, history/replay traversal, lifecycle readiness, or generated-digest behavior.
- **FR219 — Trace provenance and freshness visibility.** Future display must show source route, `trace_id`, retrieved-at/freshness, authority, linked task/event/session identifiers when returned as data, and degraded-state metadata.
- **FR220 — Trace state semantics.** Future implementation must distinguish healthy, empty/unavailable, partial, stale, unauthorized, invalid-shape, non-2xx, and backend-unavailable states without rendering degraded data as authoritative success.
- **FR221 — No hidden writes/effects.** Future implementation tests must prove no writer imports, lifecycle helper imports, snapshot creation, background job dispatch, idempotency writes, cache-warming write paths, archive mutation, manifest mutation, side-effectful reads, replay execution, trace traversal jobs, or mutation/control vocabulary.
- **FR222 — No behavior change in Story 104.1.** Story 104.1 must not change runtime/package/API/MCP/service/script/dashboard/test/deployment/dependency/lockfile/CI behavior.

## Non-functional requirements

- **NFR-S37 — Trace-correlation fail-closed safety.** Missing, stale, unauthorized, unavailable, invalid, empty, partial, or backend-unavailable trace data renders bounded non-authoritative or explicit empty/unavailable copy.
- **NFR-S38 — Read-only-by-effect enforcement.** Future trace runtime must be visibility-only and cannot import/call write, lifecycle, snapshot, job-dispatch, cache-warming, archive, replay, traversal, or mutation helpers.
- **NFR-O29 — Trace-correlation auditability.** Every future displayed value is traceable to `GET /v1/trace/{trace_id}`, `trace_id`, freshness, authority, and returned provenance metadata.
- **NFR-M25 — Test-first runtime maintainability.** Future implementation must add runtime-boundary tests before or with any browser/runtime wiring.
- **NFR-R25 — Safe trace degradation.** Backend failure, invalid/unavailable data, stale state, partial data, empty state, or unauthorized responses must degrade to explicit non-authoritative or empty/unavailable copy.

## Acceptance criteria

1. Phase 25 PRD, architecture, and epics artifacts exist and define Trace correlation live-read route-selection scope.
2. Story 104.1 artifact records lifecycle evidence, exact route selection, non-goals, future test obligations, verification plan, and completion criteria.
3. Sprint status sets `current_phase: 25`, opens Epic 104 / Story 104.1, preserves Epic 103 done, records Epic 103 retrospective done, and records newest-first audit evidence.
4. Tracked product diff is limited to the Phase 24 retrospective, Phase 25 planning artifacts, Story 104.1 artifact, and sprint-status update.
5. Story 104.1 explicitly excludes runtime implementation, broad dashboard live wiring, backend/API expansion, trace search/list/discovery, history/replay/lifecycle/task-list/search/discovery/aggregate/session/digest contracts, mutation/control affordances, dependency/lockfile/CI/deployment changes, and test/runtime code changes.
6. Follow-on Phase 25 epics sequence docs/status opening first, trace-correlation runtime-boundary tests/implementation second, final closure third.

## Required follow-on gates before trace-correlation runtime completion

Before Trace correlation runtime wiring can be marked complete, a future story must produce and verify:

1. Runtime-boundary tests proving only `/v1/trace/{trace_id}` is reachable for this slice.
2. GET-only method checks and no POST/PUT/PATCH/DELETE calls.
3. Explicit visible `trace_id` identifier handling with no trace search/list/discovery behavior.
4. No event/task/session identifier can become a hidden trace route selector without first exposing an explicit visible `trace_id` source.
5. No history/replay, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, stream, generated live data, or control route reachability.
6. Selector-drift tests proving `event_id`, `task_id`, and `session_id` are returned/display metadata only and cannot drive hidden trace lookup, replay/history lookup, lifecycle lookup, or discovery.
7. Semantic-drift tests proving Trace correlation rendering does not join, infer, summarize, or traverse history/replay/lifecycle/session/aggregate/digest data beyond the single returned trace payload.
8. Visible source route, `trace_id`, retrieved-at/freshness, authority, linked identifiers, and degraded-state metadata.
9. Healthy/empty/unavailable/partial/stale/unauthorized/invalid-shape/non-2xx/backend-unavailable rendering semantics.
10. Static import/grep guards for writer imports, lifecycle helper imports, snapshot creation, background job dispatch, idempotency writes, cache-warming write paths, archive mutation, manifest mutation, replay execution, traversal jobs, side-effectful reads, and mutation/control vocabulary.
11. Independent code-reviewer APPROVE and architect CLEAR before completion.
12. Push and GitHub Actions CI green before Phase 25 can claim trace-correlation runtime completion.
