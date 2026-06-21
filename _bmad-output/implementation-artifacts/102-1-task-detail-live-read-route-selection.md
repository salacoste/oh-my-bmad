# Story 102.1 — Phase 23 Task Detail Live-Read Route Selection

## Status
Done — docs/status-only Phase 23 / Epic 102 opening and next-route selection.

## Selected next route family
Story 102.1 selects exactly one future live-read route family:

- Task detail: `GET /v1/tasks/{task_id}`

This route is the next boundary target because it is an existing approved read contract with a single required identifier, `task_id`, and is narrower than event timeline, transitions, trace, history, replay, lifecycle, aggregate/session, or digest surfaces.

## Scope delivered
Story 102.1 opens Phase 23 and creates the standard docs/status artifact set:

- `_bmad-output/planning-artifacts/phase-23-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-23-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-23-epics.md`
- `_bmad-output/implementation-artifacts/102-1-task-detail-live-read-route-selection.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

Story 102.1 does **not** implement browser/runtime/API wiring. It records route selection, Phase 23 product and architecture boundaries, future story sequencing, mandatory future tests, and explicit non-authorization.

## Phase status
Phase 22 remains closed as Health/readiness `GET /v1/health` runtime-boundary complete.

Phase 23 opens as Task detail live-read runtime-boundary planning without claiming task-detail runtime completion.

## Mandatory tests for a later Task detail runtime implementation
A later implementation story, expected as Story 102.2 or equivalent, must prove all of the following before it can be marked done:

1. Dashboard runtime can reach only the approved `GET /v1/tasks/{task_id}` route for this slice.
2. Dashboard runtime calls are GET-only; POST, PUT, PATCH, and DELETE calls fail tests.
3. `task_id` is required, visible, and not obtained through hidden task-list, task-search, session, aggregate, or discovery behavior.
4. No aggregate overview, session-list, digest, task-list/search/discovery, event timeline, transitions, trace, history, replay, lifecycle, or generated live-data contract is introduced.
5. No forms, buttons, inputs, operator controls, mutation/control vocabulary, or destructive lifecycle affordance is introduced.
6. No hidden HTTP client exists beyond the explicitly approved task-detail read mechanism.
7. Healthy, unavailable, stale, unauthorized, and backend-unavailable responses render bounded authoritative or non-authoritative copy according to the task-detail contract.
8. Source route, `task_id`, retrieved-at/freshness, authority, and degraded-state metadata remain visible.
9. Static import/grep guards reject writer imports, lifecycle helper imports, snapshot creation, background job dispatch, idempotency writes, cache-warming write paths, archive mutation, manifest mutation, side-effectful reads, and mutation/control vocabulary.
10. Existing health runtime-boundary and dashboard static/read-only/fixture regressions remain green.
11. Independent code-reviewer `APPROVE` and architect `CLEAR` gates pass.
12. Push and GitHub Actions CI are green before Phase 23 can claim task-detail runtime completion.

## Explicit non-authorization
Story 102.1 authorizes none of the following:

- Runtime implementation of `GET /v1/tasks/{task_id}` inside Story 102.1.
- Broad live dashboard runtime wiring.
- Task event timeline, transitions, trace, history, replay, lifecycle, aggregate overview, session-list, digest, task-list, task-search, task-discovery, stream, or generated live-data contracts.
- Mutation/control/destructive lifecycle affordance.
- POST, PUT, PATCH, or DELETE dashboard calls.
- Backend/API route expansion.
- Additional browser runtime modules, inline scripts, dynamic imports, workers, service workers, polling, streaming, subscriptions, storage/cache, beacons, WebSockets, EventSource, XMLHttpRequest, hidden HTTP clients, forms, buttons, inputs, or operator controls.
- Writer imports, lifecycle helper imports, snapshot creation, background job dispatch, idempotency writes, cache-warming writes, archive mutation, manifest mutation, or side-effectful reads.
- Dependency, lockfile, deployment, CI, package, service, MCP, runtime framework, or generated-data changes.
- Selection or implementation of any route family beyond Task detail `GET /v1/tasks/{task_id}`.

## Changed-file scope
Story 102.1 implementation scope is limited to docs/status planning files:

- `_bmad-output/planning-artifacts/phase-23-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-23-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-23-epics.md`
- `_bmad-output/implementation-artifacts/102-1-task-detail-live-read-route-selection.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

Workflow-only planning/review evidence remains under `.omx/`.

## Review gate evidence

- Initial Architect review blocked implementation until the full Phase 23 PRD/architecture/epics triad was included and future no-hidden-write/static import-grep guardrails were explicit.
- Revised Architect review: `ARCHITECTURAL_STATUS: CLEAR`, required changes: none.
- Critic review: `CRITIC_STATUS: PROCEED`.

## Verification plan

- Markdown/content checks verify Phase 23 opening, exact route selection, future implementation requirements, and forbidden-surface denials.
- Sprint-status YAML parses and records Phase 23 / Epic 102 / Story 102.1 lifecycle newest-first without reopening Phase 22.
- Changed-file allowlist remains docs/status-only, excluding `.omx/` workflow artifacts.
- `git diff --check` passes.

## AI slop cleanup report

Scope: Story 102.1 changed files listed above.

Behavior lock:
- Content checks verify exact Task detail `GET /v1/tasks/{task_id}` selection, Phase 23 opening, future runtime-boundary test obligations, and forbidden-surface denials.
- Sprint-status YAML parse and lifecycle checks pass.
- Changed-file allowlist passes for the five docs/status product files, excluding `.omx/` workflow artifacts.
- `git diff --check` passes.

Cleanup plan:
1. Keep scope docs/status-only.
2. Preserve explicit no-implementation and no-authorization wording even if repetitive.
3. Avoid selecting any additional route family or adding implementation scaffolding.
4. Rerun verification after any wording change.

Fallback/slop findings:
- No masking fallback, broad compatibility shim, swallowed error, silent default, speculative runtime path, hidden HTTP client, dependency, or source-code change found.
- Repeated guardrail wording is intentional risk mitigation, not duplication to remove.

Remaining risk:
- The phrase route selection can be misread as implementation authorization. The mitigation is explicit closure wording: Story 102.1 only selects the future route family; runtime wiring requires a later separate story with tests, review, push, and CI evidence.
