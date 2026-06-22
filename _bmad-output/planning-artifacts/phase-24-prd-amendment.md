# Phase 24 PRD Amendment — Event Timeline / Transitions Live-Read Route Selection

## Summary

Phase 24 opens the next dashboard live-read planning branch after Phase 23 closed the Task detail `GET /v1/tasks/{task_id}` runtime boundary. Phase 24 selects exactly one next future route family:

- Event timeline / transitions:
  - `GET /v1/tasks/{task_id}/events`
  - `GET /v1/tasks/{task_id}/transitions`

This PRD amendment is a product planning artifact. Story 103.1 does not add runtime behavior, dashboard JavaScript, browser network calls, backend/API routes, tests, dependencies, CI/deployment changes, services, MCP changes, generated live data, or mutation/control surfaces.

## Problem

The dashboard now has narrow live-read runtime proofs for Health/readiness and Task detail. The next useful operator visibility step is task-scoped event and transition visibility, but implementing it without a phase-level product boundary risks accidentally introducing task discovery, trace correlation, history/replay, lifecycle readiness, aggregate/session/digest, broad runtime modules, hidden writes, or control-plane behavior.

Phase 24 therefore starts with docs/status-only route-family selection and guardrail definition. It chooses the smallest next task-scoped route family that preserves the already proven explicit `task_id` boundary.

## Goals

- Open Phase 24 / Epic 103 as the next post-task-detail live-read planning branch.
- Select exactly `GET /v1/tasks/{task_id}/events` and `GET /v1/tasks/{task_id}/transitions` as the next future route family.
- Preserve read-only-by-effect semantics: no hidden writes, no side-effectful reads, no cache-warming writes, no background dispatch, no mutation/control behavior.
- Require a later implementation story to prove route/method/module/effect boundaries before claiming runtime completion.
- Keep trace, history/replay, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, and control surfaces out of this route-selection story.

## Scope

IN for Story 103.1:

- Phase 24 PRD amendment.
- Phase 24 architecture amendment.
- Phase 24 epics.
- Story 103.1 lifecycle artifact.
- Sprint-status update opening Phase 24 / Epic 103 and marking Story 103.1 done.
- Future implementation requirements for the Event timeline / transitions route family.

OUT for Story 103.1:

- Runtime implementation of the selected routes.
- Browser `fetch`, XHR, WebSocket, EventSource, polling, frontend scripts, hidden HTTP clients, or dashboard/static behavior changes.
- Backend/API route expansion or server contract changes.
- Test-code changes.
- Trace, history, replay, lifecycle readiness, task-list/search/discovery, aggregate overview, session-list, digest, stream, generated live data, or control contracts.
- `event_id` as route input; event identifiers may be row metadata only in a future implementation.
- Mutation/control/destructive lifecycle affordances including approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled jobs, credentialed production operation, credential entry, token minting, public sharing, OAuth, external hosting, or multi-user auth.
- Dependencies, lockfiles, deployment, CI, package, service, MCP, runtime framework, or generated-data changes.

## Functional requirements

- **FR207 — Phase 24 event/transition scope.** The repository records Phase 24 as the product-scope gate for the next narrow dashboard live-read route family after Task detail.
- **FR208 — Exact route-family selection.** Story 103.1 selects exactly `GET /v1/tasks/{task_id}/events` and `GET /v1/tasks/{task_id}/transitions`, and selects no other live-read route family.
- **FR209 — Separate implementation story.** Runtime wiring for the selected route family requires a later separately approved story, expected as Story 103.2 or equivalent.
- **FR210 — Task identifier boundary.** Future implementation must require explicit `task_id` input/context and must not introduce task-list, task-search, session, aggregate, digest, trace, history, replay, lifecycle, or discovery behavior.
- **FR211 — Event/transition provenance and freshness visibility.** Future display must show source route, `task_id`, retrieved-at/freshness, authority, row count or empty-state evidence, and degraded-state metadata.
- **FR212 — Event/transition state semantics.** Future implementation must distinguish healthy, empty, unavailable, stale, unauthorized, invalid-shape, and backend-unavailable states without rendering degraded data as authoritative success.
- **FR213 — No hidden writes/effects.** Future implementation tests must prove no writer imports, lifecycle helper imports, snapshot creation, background job dispatch, idempotency writes, cache-warming write paths, archive mutation, manifest mutation, side-effectful reads, or mutation/control vocabulary.
- **FR214 — No behavior change in Story 103.1.** Story 103.1 must not change runtime/package/API/MCP/service/script/dashboard/test/deployment/dependency/lockfile/CI behavior.

## Non-functional requirements

- **NFR-S35 — Event/transition fail-closed safety.** Missing, stale, unauthorized, unavailable, invalid, empty, or backend-unavailable event/transition data renders bounded non-authoritative or explicit empty-state copy.
- **NFR-S36 — Read-only-by-effect enforcement.** Future event/transition runtime must be visibility-only and cannot import/call write, lifecycle, snapshot, job-dispatch, cache-warming, archive, or mutation helpers.
- **NFR-O28 — Event/transition auditability.** Every future displayed value is traceable to the selected route, `task_id`, freshness, and authority metadata.
- **NFR-M24 — Test-first runtime maintainability.** Future implementation must add runtime-boundary tests before or with any browser/runtime wiring.
- **NFR-R24 — Safe event/transition degradation.** Backend failure, invalid/unavailable data, stale state, empty state, or unauthorized responses must degrade to explicit non-authoritative or empty copy.

## Acceptance criteria

1. Phase 24 PRD, architecture, and epics artifacts exist and define Event timeline / transitions live-read route-selection scope.
2. Story 103.1 artifact records lifecycle evidence, exact route selection, non-goals, future test obligations, verification plan, and completion criteria.
3. Sprint status sets `current_phase: 24`, opens Epic 103 / Story 103.1, preserves Epic 102 done, records Epic 102 retrospective done, and records newest-first audit evidence.
4. Tracked product diff is limited to the Phase 23 retrospective, Phase 24 planning artifacts, Story 103.1 artifact, and sprint-status update.
5. Story 103.1 explicitly excludes runtime implementation, broad dashboard live wiring, backend/API expansion, trace/history/replay/lifecycle/task-list/search/discovery/aggregate/session/digest contracts, mutation/control affordances, dependency/lockfile/CI/deployment changes, and test/runtime code changes.
6. Follow-on Phase 24 epics sequence docs/status opening first, event/transition runtime-boundary tests/implementation second, final closure third.

## Required follow-on gates before event/transition runtime completion

Before Event timeline / transitions runtime wiring can be marked complete, a future story must produce and verify:

1. Runtime-boundary tests proving only `/v1/tasks/{task_id}/events` and `/v1/tasks/{task_id}/transitions` are reachable for this slice.
2. GET-only method checks and no POST/PUT/PATCH/DELETE calls.
3. Explicit `task_id` identifier handling with no task-list/search/discovery behavior.
4. No trace, history, replay, lifecycle, aggregate/session/digest, stream, generated live data, or control route reachability.
5. Selector-drift tests proving `task_id` is the only route selector and `event_id` cannot drive fetch construction, hidden filtering, trace lookup, history lookup, replay lookup, lifecycle lookup, or discovery.
6. Semantic-drift tests proving Event timeline / transitions rendering does not join, infer, or summarize trace/history/replay/lifecycle/session/aggregate/digest data.
7. Visible source routes, `task_id`, retrieved-at/freshness, authority, row-count/empty-state, and degraded-state metadata.
8. Healthy/empty/unavailable/stale/unauthorized/invalid-shape/backend-unavailable rendering semantics.
9. Static import/grep guards for writer imports, lifecycle helper imports, snapshot creation, background job dispatch, idempotency writes, cache-warming write paths, archive mutation, manifest mutation, side-effectful reads, and mutation/control vocabulary.
10. Independent code-reviewer APPROVE and architect CLEAR before completion.
11. Push and GitHub Actions CI green before Phase 24 can claim event/transition runtime completion.
