# Story 104.1 — Phase 25 Trace Correlation Live-Read Route Selection

## Status

Done — docs/status-only Phase 25 / Epic 104 opening and next-route selection.

## Selected next route family

Story 104.1 selects exactly one future live-read route family:

- Trace correlation:
  - `GET /v1/trace/{trace_id}`

This route family is the next boundary target because it naturally follows Event timeline / transitions: event and transition rows may display `trace_id` as provenance metadata. It remains narrower than history/replay, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, and mutation/control surfaces.

## Scope delivered

Story 104.1 opens Phase 25 and creates the standard docs/status artifact set:

- `_bmad-output/planning-artifacts/phase-25-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-25-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-25-epics.md`
- `_bmad-output/implementation-artifacts/104-1-trace-correlation-live-read-route-selection.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

It also records the requested Phase 24 / Epic 103 retrospective:

- `_bmad-output/implementation-artifacts/epic-103-retro-2026-06-22.md`

Story 104.1 does **not** implement browser/runtime/API wiring. It records route selection, Phase 25 product and architecture boundaries, future story sequencing, mandatory future tests, and explicit non-authorization.

## Phase status

Phase 24 remains closed as Event timeline / transitions `GET /v1/tasks/{task_id}/events` and `GET /v1/tasks/{task_id}/transitions` runtime-boundary complete.

Phase 25 opens as Trace correlation live-read route-family planning without claiming runtime completion.

## Mandatory tests for a later Trace correlation runtime implementation

A later implementation story, expected as Story 104.2 or equivalent, must prove all of the following before it can be marked done:

1. Dashboard runtime can reach only the approved `GET /v1/trace/{trace_id}` route for this slice.
2. Dashboard runtime calls are GET-only; POST, PUT, PATCH, and DELETE calls fail tests.
3. `trace_id` is required, visible, and not obtained through hidden trace search/list/discovery, task-list/search/discovery, event lookup, session traversal, aggregate synthesis, history/replay traversal, lifecycle lookup, generated digest, storage, URL query/hash, hidden `data-*`, or log parsing behavior.
4. Event, task, and session identifiers are display/provenance metadata only, not route inputs, hidden selectors, hidden filters, replay/history lookup keys, lifecycle lookup keys, aggregate/session lookup keys, or discovery sources.
5. Selector-drift tests prove `trace_id` is the only fetch-construction selector for the approved route.
6. Semantic-drift tests prove the panel does not enrich, join, infer, summarize, or traverse through history, replay, lifecycle, session, aggregate, digest, generated live data, event timeline, transitions, or discovery sources.
7. No history/replay, lifecycle readiness, aggregate overview, session-list, digest, task-list/search/discovery, trace search/list/discovery, stream, or generated live-data contract is introduced.
8. No forms, buttons, inputs, operator controls, mutation/control vocabulary, or destructive lifecycle affordance is introduced unless a later implementation story explicitly approves a visible read-only trace_id entry control and proves it cannot mutate external state.
9. No hidden HTTP client exists beyond the explicitly approved trace-correlation read mechanism.
10. Healthy, empty/unavailable, partial, stale, unauthorized, invalid-shape, non-2xx, network-failure, and backend-unavailable responses render bounded authoritative, empty/unavailable, partial, or non-authoritative copy according to the trace-correlation contract.
11. Source route, `trace_id`, retrieved-at/freshness, authority, linked identifiers, and degraded-state metadata remain visible.
12. Static import/grep guards reject writer imports, lifecycle helper imports, replay execution helpers, traversal jobs, snapshot creation, background job dispatch, idempotency writes, cache-warming write paths, archive mutation, manifest mutation, side-effectful reads, and mutation/control vocabulary.
13. Existing health, task-detail, and event/transition runtime-boundary regressions remain green.
14. Independent code-reviewer `APPROVE` and architect `CLEAR` gates pass.
15. Push and GitHub Actions CI are green before Phase 25 can claim trace-correlation runtime completion.

## Explicit non-authorization

Story 104.1 authorizes none of the following:

- Runtime implementation of the selected route inside Story 104.1.
- Broad live dashboard runtime wiring.
- Trace search/list/discovery, automatic trace traversal, history, replay, lifecycle readiness, aggregate overview, session-list, digest, task-list, task-search, task-discovery, stream, generated live data, or control contracts.
- `event_id`, `task_id`, or `session_id` as route input, hidden selector, or discovery mechanism.
- Mutation/control/destructive lifecycle affordance.
- POST, PUT, PATCH, or DELETE dashboard calls.
- Backend/API route expansion.
- Additional browser runtime modules, inline scripts, dynamic imports, workers, service workers, polling, streaming, subscriptions, storage/cache, beacons, WebSockets, EventSource, XMLHttpRequest, hidden HTTP clients, forms, buttons, inputs, or operator controls.
- Writer imports, lifecycle helper imports, replay execution helpers, traversal jobs, snapshot creation, background job dispatch, idempotency writes, cache-warming writes, archive mutation, manifest mutation, or side-effectful reads.
- Dependency, lockfile, deployment, CI, package, service, MCP, runtime framework, or generated-data changes.
- Selection or implementation of any route family beyond Trace correlation.

## Changed-file scope

Story 104.1 product scope is limited to docs/status planning files:

- `_bmad-output/implementation-artifacts/epic-103-retro-2026-06-22.md`
- `_bmad-output/planning-artifacts/phase-25-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-25-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-25-epics.md`
- `_bmad-output/implementation-artifacts/104-1-trace-correlation-live-read-route-selection.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

Workflow-only planning/review evidence remains under `.omx/`.

## Ralplan consensus evidence

- Deep-interview gate completed without needing a user question because the activation prompt and repository evidence resolved scope, candidate selection, non-goals, and stop condition.
- Architect lane `019ef148-bfaa-7e41-8a12-8829d4cd908e`: APPROVE / CLEAR for the Trace correlation planning slice.
- Critic lane `019ef14a-e19e-7d20-b5bd-fc5885defd20`: APPROVE and ready for docs/status-only Ultragoal execution.

## Verification plan

- Markdown/content checks verify Phase 25 opening, exact Trace correlation selection, future implementation requirements, and forbidden-surface denials.
- Sprint-status YAML parses and records Phase 25 / Epic 104 / Story 104.1 lifecycle newest-first without reopening Phase 24.
- Changed-file allowlist remains docs/status-only, excluding `.omx/` workflow artifacts.
- `git diff --check` passes.

## AI slop cleanup report

Scope: Story 104.1 changed files listed above.

Behavior lock:
- Content checks verify exact Trace correlation selection, Phase 25 opening, future runtime-boundary test obligations, and forbidden-surface denials.
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
- The phrase trace correlation can be misread as trace search/list or history/replay traversal. The mitigation is explicit selector/semantic-drift wording: Story 104.1 only selects `GET /v1/trace/{trace_id}`; `event_id`, `task_id`, and `session_id` remain display/provenance metadata only; history/replay, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, and control surfaces remain separate future route families.
