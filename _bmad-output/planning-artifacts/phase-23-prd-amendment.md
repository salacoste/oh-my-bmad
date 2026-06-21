# Phase 23 PRD Amendment — Task Detail Live-Read Runtime Boundary

## Summary

Phase 23 opens the **Task detail live-read runtime boundary** branch after Phase 22 closed the Health/readiness `GET /v1/health` boundary. Phase 23 selects exactly one next future live-read route family:

- `GET /v1/tasks/{task_id}`

This PRD amendment is a product planning artifact. Story 102.1 does not add runtime behavior, dashboard JavaScript, browser network calls, backend/API routes, test code, dependencies, CI/deployment changes, services, MCP changes, generated live data, or operator mutation/control surfaces.

## Problem

The dashboard now has one proven live-read runtime boundary for health/readiness. The next useful operator visibility step is task-specific detail, but implementing it without a phase-level product boundary risks accidentally introducing task lists, aggregate/session discovery, event timelines, trace/history/replay surfaces, broad runtime modules, hidden writes, or control-plane behavior.

Phase 23 therefore starts with docs/status-only route-family selection and guardrail definition. It chooses the smallest next route boundary: a single approved GET route with one required identifier, `task_id`.

## Goals

- Open Phase 23 / Epic 102 as the next post-health live-read planning branch.
- Select exactly `GET /v1/tasks/{task_id}` as the next future live-read route family.
- Preserve read-only-by-effect semantics: no hidden writes, no side-effectful reads, no cache-warming writes, no background dispatch, no mutation/control behavior.
- Require a later implementation story to prove route/method/module/effect boundaries before claiming runtime completion.
- Keep aggregate overview, session list, task list/search/discovery, digest, event timeline, trace, history, replay, and lifecycle surfaces out of this route-selection story.

## Scope

IN for Story 102.1:

- Phase 23 PRD amendment.
- Phase 23 architecture amendment.
- Phase 23 epics.
- Story 102.1 lifecycle artifact.
- Sprint-status update opening Phase 23 / Epic 102 and marking Story 102.1 done.
- Future implementation requirements for Task detail `GET /v1/tasks/{task_id}`.

OUT for Story 102.1:

- Runtime implementation of `GET /v1/tasks/{task_id}`.
- Browser `fetch`, XHR, WebSocket, EventSource, polling, frontend scripts, hidden HTTP clients, or dashboard/static behavior changes.
- Backend/API route expansion or server contract changes.
- Test-code changes.
- Aggregate overview, session-list, task-list/search/discovery, digest, event timeline, task transitions, trace, history, replay, lifecycle, or generated live-data contracts.
- Mutation/control/destructive lifecycle affordances including approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled jobs, credentialed production operation, credential entry, token minting, public sharing, OAuth, external hosting, or multi-user auth.
- Dependencies, lockfiles, deployment, CI, package, service, MCP, runtime framework, or generated-data changes.

## Functional requirements

- **FR191 — Phase 23 task-detail scope.** The repository records Phase 23 as the product-scope gate for the next narrow dashboard live-read runtime boundary after Health/readiness.
- **FR192 — Exact route selection.** Story 102.1 selects exactly `GET /v1/tasks/{task_id}` as the next future route family and selects no other live-read route family.
- **FR193 — Separate implementation story.** Runtime wiring for `GET /v1/tasks/{task_id}` requires a later separately approved story, expected as Story 102.2 or equivalent.
- **FR194 — Task identifier boundary.** Future implementation must require explicit `task_id` input/context and must not introduce task-list, task-search, session, aggregate, or discovery behavior.
- **FR195 — Provenance and freshness visibility.** Future task-detail display must show source route, `task_id`, retrieved-at/freshness, authority, and degraded-state metadata.
- **FR196 — Task-detail state semantics.** Future implementation must distinguish healthy, unavailable, stale, unauthorized, and backend-unavailable states without rendering degraded data as authoritative success.
- **FR197 — No hidden writes/effects.** Future implementation tests must prove no writer imports, lifecycle helper imports, snapshot creation, background job dispatch, idempotency writes, cache-warming write paths, archive mutation, manifest mutation, or side-effectful reads.
- **FR198 — No behavior change in Story 102.1.** Story 102.1 must not change runtime/package/API/MCP/service/script/dashboard/test/deployment/dependency/lockfile/CI behavior.

## Non-functional requirements

- **NFR-S33 — Task-detail fail-closed safety.** Missing, stale, unauthorized, unavailable, or backend-unavailable task detail renders bounded non-authoritative copy.
- **NFR-S34 — Read-only-by-effect enforcement.** Future task-detail runtime must be visibility-only and cannot import/call write, lifecycle, snapshot, job-dispatch, cache-warming, archive, or mutation helpers.
- **NFR-O27 — Task-detail auditability.** Every future task-detail displayed value is traceable to `GET /v1/tasks/{task_id}`, `task_id`, freshness, and authority metadata.
- **NFR-M23 — Test-first runtime maintainability.** Future implementation must add runtime-boundary tests before or with any browser/runtime wiring.
- **NFR-R23 — Safe task-detail degradation.** Backend failure, invalid/unavailable data, stale state, or unauthorized responses must degrade to explicit non-authoritative copy.

## Acceptance criteria

1. Phase 23 PRD, architecture, and epics artifacts exist and define Task detail live-read route selection scope.
2. Story 102.1 artifact records lifecycle evidence, exact route selection, non-goals, future test obligations, verification plan, and completion criteria.
3. Sprint status sets `current_phase: 23`, opens Epic 102 / Story 102.1, preserves Epic 101 done, and records newest-first audit evidence.
4. Tracked product diff is limited to the five Story 102.1 docs/status files.
5. Story 102.1 explicitly excludes runtime implementation, broad dashboard live wiring, backend/API expansion, aggregate/session/digest/task-list/search/discovery/event/trace/history/replay/lifecycle contracts, mutation/control affordances, dependency/lockfile/CI/deployment changes, and test/runtime code changes.
6. Follow-on Phase 23 epics sequence docs/status opening first, task-detail runtime-boundary tests/implementation second, final closure third.

## Required follow-on gates before task-detail runtime completion

Before `GET /v1/tasks/{task_id}` runtime wiring can be marked complete, a future story must produce and verify:

1. Runtime-boundary tests proving only `/v1/tasks/{task_id}` is reachable for this slice.
2. GET-only method checks and no POST/PUT/PATCH/DELETE calls.
3. Explicit `task_id` identifier handling with no task-list/search/discovery behavior.
4. No aggregate/session/digest/event/trace/history/replay/lifecycle route reachability.
5. Visible source route, `task_id`, retrieved-at/freshness, authority, and degraded-state metadata.
6. Healthy/unavailable/stale/unauthorized/backend-unavailable rendering semantics.
7. Static import/grep guards for writer imports, lifecycle helper imports, snapshot creation, background job dispatch, idempotency writes, cache-warming write paths, archive mutation, manifest mutation, side-effectful reads, and mutation/control vocabulary.
8. Independent code-reviewer APPROVE and architect CLEAR before completion.
9. Push and GitHub Actions CI green before Phase 23 can claim task-detail runtime completion.
