# Story 89.1: Overview/task list with explicit unavailable fallback

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a single-operator maintainer,
I want the dashboard overview and task list to show task visibility only from an approved safe aggregate read, or otherwise show an explicit unavailable state,
so that Phase 19 can add situational awareness without inventing data, calling mutating discovery paths, or weakening the read-only boundary.

## Acceptance Criteria

1. The overview/task list does not infer, synthesize, scrape, or discover task lists through mutating operations, side-effectful reads, cache-warming writes, background jobs, or lifecycle/control helpers.
2. If no existing safe aggregate task read is confirmed and no separately approved future read contract exists, the overview/task-list panel renders an explicit unavailable state that explains the missing safe aggregate read and links to Audit and Help guidance.
3. If a future implementation displays task rows, each row includes provenance/source, timestamp or freshness state, task state, and the route/reference that produced the data.
4. The panel distinguishes unavailable read, loading, empty successful read, stale/partial data, permission/configuration failure, and read error states without implying mutation occurred.
5. The panel exposes no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, production operation, credential entry, or other mutation/control affordance.
6. Story 89.1 does not approve any new aggregate task-list API, route, schema, live data wiring, dependency selection, deployment/CI change, lockfile change, or runtime behavior change. Only an existing safe aggregate read or a separately approved future read contract may be used by a later implementation pass.
7. This create-story/status slice creates implementation guidance only; future dev-story work must provide tests and review evidence before moving the story beyond `ready-for-dev`.

## Tasks / Subtasks

- [ ] Confirm the aggregate task-list read basis before implementation (AC: 1, 2, 6)
  - [ ] Search existing registry-api/replay/dashboard surfaces for a safe aggregate task read; document the exact route/reference if one exists.
  - [ ] If no safe aggregate read exists, keep the panel in explicit unavailable state by default.
  - [ ] Do not create a new aggregate `GET` route in this story unless a separately approved future read contract exists and proves no hidden writes, cache-warming writes/read-side effects, or background-job dispatch.
- [ ] Implement or refine overview/task-list presentation only inside the approved dashboard surface (AC: 2, 3, 4)
  - [ ] Reuse the existing static dashboard shell location and conventions from Story 88.1 unless the implementation gate records a better repo-local rationale.
  - [ ] Preserve the persistent read-only banner and unavailable-state vocabulary already established by Story 88.1.
  - [ ] Add or refine overview/task-list copy so unavailable, empty-success, stale/partial, read-error, and permission/configuration states are visually and textually distinct.
  - [ ] Include Audit and Help references in the unavailable aggregate-read state.
- [ ] Preserve read-only/no-mutation boundaries (AC: 1, 5, 6)
  - [ ] Do not add buttons, forms, event handlers, scripts, live polling, mutation routes, lifecycle controls, credential entry, or production operation controls.
  - [ ] Do not import or call registry/event-log writers, lifecycle apply/prune helpers, snapshot creation, archive mutation, cache writers, or job-dispatch helpers.
  - [ ] Treat local filter reset/adjustment, if implemented later, as ephemeral client/render state only; it must not issue writes or background jobs.
- [ ] Add or update tests in the future implementation pass (AC: all)
  - [ ] Extend existing dashboard static tests rather than bypassing them.
  - [ ] Assert explicit unavailable fallback when aggregate task read is not approved.
  - [ ] Assert future task rows, if any, include provenance/source, timestamp/freshness, state, and route/reference.
  - [ ] Assert no scripts, event handlers, forms, actionable controls, unsafe hrefs, live API calls, mutation vocabulary outside required negative-control context, or non-GET route/method contexts are introduced.
  - [ ] Keep Story 88.2 route/method and no-mutation guard coverage green.
- [ ] Validate the implementation before review (AC: all)
  - [ ] Run focused dashboard tests: `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py` plus any new Story 89.1 tests.
  - [ ] Run `uv run ruff format --check .` and `uv run ruff check .`.
  - [ ] Run broader regression when runtime/source changes occur: `uv run pytest -q -m "not slow"`.
  - [ ] Record docs/status or implementation code-review `APPROVE`, architect `CLEAR`, and UltraQA evidence before marking done.

## Dev Notes

### Governing FR/NFR

- **FR169 — Read-only dashboard scope**: the dashboard is an operator visibility surface, not a control plane.
- **FR170 — Task and session visibility**: current and historical task/session state should be inspectable where safe reads expose it.
- **FR173 — Safe error and empty states**: distinguish no data, loading, invalid configuration, permission/configuration failure, stale data, and internal errors.
- **FR174 — No behavior change in planning/create-story slices**: this story-context creation pass changes documentation/status only.
- **NFR-S27 — Read-only by construction**: no mutating HTTP methods, approval forms, lifecycle apply controls, credential entry, or privileged operator actions.
- **NFR-O24 — Provenance-first display**: displayed state identifies source category, route/reference, freshness, and confidence.
- **NFR-M20 — Existing contract reuse**: prefer existing registry API/read surfaces; new contracts require separate approval.
- **NFR-R20 — Fail-safe visibility**: failed or unavailable reads show bounded explicit uncertainty, not authoritative partial data.

### Read-surface basis

Story 89.1 is **explicit unavailable state by default** for aggregate task listing unless the implementation pass confirms an existing safe aggregate task read or references a separately approved future read contract.

Known existing safe reads from the Phase 19 architecture amendment are task-detail/event/transition/trace/replay/health oriented, including `GET /v1/tasks/{task_id}`, `GET /v1/tasks/{task_id}/events`, `GET /v1/tasks/{task_id}/transitions`, `GET /v1/trace/{trace_id}`, `GET /v1/events/replay`, `GET /v1/events/replay/validate`, `GET /v1/events/replay/snapshots`, and `GET /v1/health`. The architecture amendment does **not** approve a new aggregate task-list route.

If no safe aggregate read exists, the future UI must state that task overview/list data is unavailable pending a safe read contract. It may link to Audit and Help guidance, but it must not synthesize task lists from mutating endpoints, filesystem scans, event-log writes, cache materializers, background jobs, or lifecycle helpers.

### UX requirements

- Overview purpose: fast situational awareness.
- Task-list purpose: browse/filter tasks.
- Required state vocabulary: loading, no tasks/empty success, healthy, attention needed, stale data, backend unavailable, filter no-results, read error, permission/configuration failure, and unavailable projection.
- If future rows are displayed, each row should include task id, status/state, current or last session when safely available, trace id when safely available, last event summary when safely available, stale/heartbeat state, terminal outcome summary, and provenance/source label.
- Narrow screens must not hide status, provenance, freshness, or attention labels.

### Safety guardrails

Only allowlisted read routes/read methods may be reachable; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, production operation, or credential entry controls.

Disabled-looking destructive controls are not acceptable placeholders. Unavailable destructive actions should remain explanatory text, not faux controls.

### Previous story intelligence

- **Story 88.1** shipped a dependency-free static dashboard shell at `dashboard/static/index.html` with persistent read-only banner, visibility-only placeholders, and unavailable-state language. Future Story 89.1 implementation should extend this surface unless the implementation gate records a different repo-local rationale.
- **Story 88.2** shipped `tests/dashboard/test_read_only_boundary.py` plus existing `tests/dashboard/test_static_shell.py` guard coverage. Future Story 89.1 implementation must keep these tests green and should extend them for overview/task-list unavailable-state semantics rather than replacing them.
- Epic 88 explicitly did not authorize live data wiring, backend routes, dependencies, deployment changes, cache-warming writes, hidden writes, background-job dispatch, mutation routes, or lifecycle/control affordances. Story 89.1 inherits those boundaries.

### Architecture compliance

- Dashboard remains a separate read-only client concept.
- Dashboard state is ephemeral client/render state unless a later architecture amendment explicitly approves a read-only server-side projection cache with no writes caused by reads.
- New `GET` contracts are exceptional and must prove read-only by effect, not just by method.
- No dashboard component may import or call registry/event-log write helpers.
- No dashboard route, action, page load, refresh, polling, or error recovery path may dispatch a job, mutate cache/state, write audit rows, or enqueue lifecycle work.

### Project Structure Notes

- Current approved dashboard implementation surface from Epic 88 is `dashboard/static/index.html` plus dashboard tests under `tests/dashboard/`.
- This create-story slice does not add or edit runtime/source/test files.
- Future implementation must keep dependency-free/static behavior unless a later approved plan explicitly justifies a frontend stack or dependency change.

### References

- `_bmad-output/planning-artifacts/phase-19-prd-amendment.md` — FR169, FR170, FR173, FR174, NFR-S27, NFR-O24, NFR-M20, NFR-R20.
- `_bmad-output/planning-artifacts/phase-19-ux-design-specification.md` — Overview, Tasks, screen states, no mutation controls, Audit and Help guidance.
- `_bmad-output/planning-artifacts/phase-19-architecture-amendment.md` — existing safe read surfaces, aggregate-read fallback decision, no-mutation verification strategy.
- `_bmad-output/planning-artifacts/phase-19-epics.md` — Epic 89 and Story 89.1 source requirements.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — status transition contract and current Phase 19 state.
- `_bmad-output/implementation-artifacts/88-1-dashboard-static-shell-read-only-banner.md` — static shell and unavailable-state precedent.
- `_bmad-output/implementation-artifacts/88-2-route-method-allowlist-no-mutation-guard-tests.md` — no-mutation and route-boundary guard precedent.

## Dev Agent Record

### Agent Model Used

TBD by future dev-story implementation pass.

### Debug Log References

- 2026-06-15: Story context created by Autopilot/BMad create-story slice. No runtime/source/test implementation was performed.

### Completion Notes List

- Ready-for-dev context created for Story 89.1.
- Epic 89 opened in sprint status through the Story 89.1 context-created audit event.
- Story 89.1 remains unimplemented; future dev-story pass owns implementation, tests, review, and UltraQA evidence.

### File List

- `_bmad-output/implementation-artifacts/89-1-overview-task-list-unavailable-fallback.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Change Log

- 2026-06-15: Created Story 89.1 context/status artifact. Status: ready-for-dev.
