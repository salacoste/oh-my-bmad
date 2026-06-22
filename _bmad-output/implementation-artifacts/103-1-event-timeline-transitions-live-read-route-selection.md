# Story 103.1 — Phase 24 Event Timeline / Transitions Live-Read Route Selection

## Status

Done — docs/status-only Phase 24 / Epic 103 opening and next-route selection.

## Selected next route family

Story 103.1 selects exactly one future live-read route family:

- Event timeline / transitions:
  - `GET /v1/tasks/{task_id}/events`
  - `GET /v1/tasks/{task_id}/transitions`

This route family is the next boundary target because it preserves the explicit `task_id` boundary proven by Task detail and is narrower than trace, history/replay, lifecycle readiness, task-list/search/discovery, aggregate/session, or digest surfaces.

## Scope delivered

Story 103.1 opens Phase 24 and creates the standard docs/status artifact set:

- `_bmad-output/planning-artifacts/phase-24-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-24-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-24-epics.md`
- `_bmad-output/implementation-artifacts/103-1-event-timeline-transitions-live-read-route-selection.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

It also records the requested Phase 23 / Epic 102 retrospective:

- `_bmad-output/implementation-artifacts/epic-102-retro-2026-06-22.md`

Story 103.1 does **not** implement browser/runtime/API wiring. It records route selection, Phase 24 product and architecture boundaries, future story sequencing, mandatory future tests, and explicit non-authorization.

## Phase status

Phase 23 remains closed as Task detail `GET /v1/tasks/{task_id}` runtime-boundary complete.

Phase 24 opens as Event timeline / transitions live-read route-family planning without claiming runtime completion.

## Mandatory tests for a later Event timeline / transitions runtime implementation

A later implementation story, expected as Story 103.2 or equivalent, must prove all of the following before it can be marked done:

1. Dashboard runtime can reach only the approved `GET /v1/tasks/{task_id}/events` and `GET /v1/tasks/{task_id}/transitions` routes for this slice.
2. Dashboard runtime calls are GET-only; POST, PUT, PATCH, and DELETE calls fail tests.
3. `task_id` is required, visible, and not obtained through hidden task-list, task-search, session, aggregate, trace, history, replay, lifecycle, or discovery behavior.
4. Event identifiers are display/provenance metadata only, not route inputs, hidden selectors, hidden filters, trace/history/replay lookup keys, lifecycle lookup keys, or discovery sources.
5. Selector-drift tests prove `task_id` is the only fetch-construction selector for both approved routes.
6. Semantic-drift tests prove the panel does not enrich, join, infer, summarize, or link through trace, history, replay, lifecycle, session, aggregate, digest, generated live data, or discovery sources.
7. No aggregate overview, session-list, digest, task-list/search/discovery, trace, history, replay, lifecycle, stream, or generated live-data contract is introduced.
8. No forms, buttons, inputs, operator controls, mutation/control vocabulary, or destructive lifecycle affordance is introduced.
9. No hidden HTTP client exists beyond the explicitly approved event/transition read mechanism.
10. Healthy, empty, unavailable, stale, unauthorized, invalid-shape, non-2xx, network-failure, and backend-unavailable responses render bounded authoritative, empty, or non-authoritative copy according to the event/transition contract.
11. Source routes, `task_id`, retrieved-at/freshness, authority, row-count/empty-state, and degraded-state metadata remain visible.
12. Static import/grep guards reject writer imports, lifecycle helper imports, snapshot creation, background job dispatch, idempotency writes, cache-warming write paths, archive mutation, manifest mutation, side-effectful reads, and mutation/control vocabulary.
13. Existing health and task-detail runtime-boundary regressions remain green.
14. Independent code-reviewer `APPROVE` and architect `CLEAR` gates pass.
15. Push and GitHub Actions CI are green before Phase 24 can claim event/transition runtime completion.

## Explicit non-authorization

Story 103.1 authorizes none of the following:

- Runtime implementation of the selected routes inside Story 103.1.
- Broad live dashboard runtime wiring.
- Trace, history, replay, lifecycle readiness, aggregate overview, session-list, digest, task-list, task-search, task-discovery, stream, generated live data, or control contracts.
- `event_id` as route input, hidden selector, or discovery mechanism.
- Mutation/control/destructive lifecycle affordance.
- POST, PUT, PATCH, or DELETE dashboard calls.
- Backend/API route expansion.
- Additional browser runtime modules, inline scripts, dynamic imports, workers, service workers, polling, streaming, subscriptions, storage/cache, beacons, WebSockets, EventSource, XMLHttpRequest, hidden HTTP clients, forms, buttons, inputs, or operator controls.
- Writer imports, lifecycle helper imports, snapshot creation, background job dispatch, idempotency writes, cache-warming writes, archive mutation, manifest mutation, or side-effectful reads.
- Dependency, lockfile, deployment, CI, package, service, MCP, runtime framework, or generated-data changes.
- Selection or implementation of any route family beyond Event timeline / transitions.

## Changed-file scope

Story 103.1 product scope is limited to docs/status planning files:

- `_bmad-output/implementation-artifacts/epic-102-retro-2026-06-22.md`
- `_bmad-output/planning-artifacts/phase-24-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-24-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-24-epics.md`
- `_bmad-output/implementation-artifacts/103-1-event-timeline-transitions-live-read-route-selection.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

Workflow-only planning/review evidence remains under `.omx/`.

## Ralplan consensus evidence

- Deep-interview gate completed without needing a user question because the activation prompt and repository evidence resolved scope and candidate selection.
- Architect lane `019eed41-3672-7531-8368-fe240c68d324`: APPROVE / CLEAR for the Event timeline / transitions planning slice.
- Critic lane `019eed43-8fdd-72a3-b5e0-cae4d6bd8b10`: APPROVE and ready for docs/status-only Ultragoal execution.

## Verification plan

- Markdown/content checks verify Phase 24 opening, exact route selection, future implementation requirements, and forbidden-surface denials.
- Sprint-status YAML parses and records Phase 24 / Epic 103 / Story 103.1 lifecycle newest-first without reopening Phase 23.
- Changed-file allowlist remains docs/status-only, excluding `.omx/` workflow artifacts.
- `git diff --check` passes.

## AI slop cleanup report

Scope: Story 103.1 changed files listed above.

Behavior lock:
- Content checks verify exact Event timeline / transitions selection, Phase 24 opening, future runtime-boundary test obligations, and forbidden-surface denials.
- Sprint-status YAML parse and lifecycle checks pass.
- Changed-file allowlist passes for the six docs/status product files, excluding `.omx/` workflow artifacts.
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
- The phrase event timeline can be misread as broad historical replay. The mitigation is explicit selector/semantic-drift wording: Story 103.1 only selects task-scoped event/transition GET routes; `event_id` remains display/provenance metadata only; history/replay, trace, lifecycle, session, aggregate, and digest remain separate future route families.
