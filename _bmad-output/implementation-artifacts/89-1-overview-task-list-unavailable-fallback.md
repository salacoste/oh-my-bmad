# Story 89.1: Overview/task list with explicit unavailable fallback

Status: done

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
7. The earlier create-story/status slice created implementation guidance only; this current implementation pass must provide tests and review evidence before moving the story beyond `review` and into `done`.

## Tasks / Subtasks

- [x] Confirm the aggregate task-list read basis before implementation (AC: 1, 2, 6)
  - [x] Search existing registry-api/replay/dashboard surfaces for a safe aggregate task read; document the exact route/reference if one exists.
  - [x] If no safe aggregate read exists, keep the panel in explicit unavailable state by default.
  - [x] Do not create a new aggregate `GET` route in this story unless a separately approved future read contract exists and proves no hidden writes, cache-warming writes/read-side effects, or background-job dispatch.
- [x] Implement or refine overview/task-list presentation only inside the approved dashboard surface (AC: 2, 3, 4)
  - [x] Reuse the existing static dashboard shell location and conventions from Story 88.1 unless the implementation gate records a better repo-local rationale.
  - [x] Preserve the persistent read-only banner and unavailable-state vocabulary already established by Story 88.1.
  - [x] Add or refine overview/task-list copy so unavailable, empty-success, stale/partial, read-error, and permission/configuration states are visually and textually distinct.
  - [x] Include Audit and Help references in the unavailable aggregate-read state.
- [x] Preserve read-only/no-mutation boundaries (AC: 1, 5, 6)
  - [x] Do not add buttons, forms, event handlers, scripts, live polling, mutation routes, lifecycle controls, credential entry, or production operation controls.
  - [x] Do not import or call registry/event-log writers, lifecycle apply/prune helpers, snapshot creation, archive mutation, cache writers, or job-dispatch helpers.
  - [x] Treat local filter reset/adjustment, if implemented later, as ephemeral client/render state only; it must not issue writes or background jobs.
- [x] Add or update tests in the future implementation pass (AC: all)
  - [x] Extend existing dashboard static tests rather than bypassing them.
  - [x] Assert explicit unavailable fallback when aggregate task read is not approved.
  - [x] Assert future task rows, if any, include provenance/source, timestamp/freshness, state, and route/reference.
  - [x] Assert no scripts, event handlers, forms, actionable controls, unsafe hrefs, live API calls, mutation vocabulary outside required negative-control context, or non-GET route/method contexts are introduced.
  - [x] Keep Story 88.2 route/method and no-mutation guard coverage green.
- [x] Validate the implementation before review (AC: all)
  - [x] Run focused dashboard tests: `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py` plus any new Story 89.1 tests.
  - [x] Run `uv run ruff format --check .` and `uv run ruff check .`.
  - [x] Run broader regression when runtime/source changes occur: `uv run pytest -q -m "not slow"`.
  - [x] Record docs/status or implementation code-review `APPROVE`, architect `CLEAR`, and UltraQA evidence before marking done.

## Dev Notes

### Governing FR/NFR

- **FR169 — Read-only dashboard scope**: the dashboard is an operator visibility surface, not a control plane.
- **FR170 — Task and session visibility**: current and historical task/session state should be inspectable where safe reads expose it.
- **FR173 — Safe error and empty states**: distinguish no data, loading, invalid configuration, permission/configuration failure, stale data, and internal errors.
- **FR174 — No behavior change in planning/create-story slices**: the earlier story-context creation pass changed documentation/status only; this implementation pass remains limited to static dashboard/test/story/status artifacts and does not change backend runtime behavior.
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
- The earlier create-story slice did not add or edit runtime/source/test files; this implementation pass edits only the approved static dashboard/test/story/status files.
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

GPT-5.4 Codex via OMX Autopilot/Ultragoal.

### Debug Log References

- 2026-06-15: Story context created by Autopilot/BMad create-story slice. No runtime/source/test implementation was performed in that earlier slice.
- 2026-06-15: Ralplan critic initially BLOCKED missing full AC4 state matrix, Story 88.1 non-regression, and safe local anchor/non-live route placeholder constraints; plan/spec revised and then Architect APPROVE/CLEAR plus Critic APPROVE.
- 2026-06-15: Red test evidence: `uv run pytest tests/dashboard/test_static_shell.py -q` failed with 4 expected Story 89.1 failures for missing `section#overview`, missing aggregate unavailable fallback copy, missing state matrix, and missing task row provenance placeholder.
- 2026-06-15: Green focused evidence: `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q` passed 16 tests.
- 2026-06-15: Full regression evidence: `uv run pytest -q -m "not slow"` passed 4151 tests, skipped 8, deselected 61.
- 2026-06-15: Final review evidence: independent code-review Recommendation APPROVE; architect Architectural Status CLEAR; UltraQA static adversarial scenarios passed 19.

### Completion Notes List

- Confirmed no approved safe aggregate task-list read exists in the current Phase 19 architecture; Story 89.1 remains fallback-only and does not add backend routes, live wiring, dependencies, or runtime behavior.
- Added static Overview panel and refined Tasks panel to state aggregate task overview/list data is unavailable because no safe aggregate task read is approved or wired.
- Added full Story 89.1 state vocabulary for unavailable read, loading, empty successful read/no tasks, stale/partial data, permission/configuration failure, and read error.
- Added safe local Audit/Help anchor references plus future row contract placeholders for provenance/source, timestamp/freshness, state, and generic route/reference.
- Preserved Story 88.1 read-only banner, existing dashboard panels, no-live-API/no-control guard behavior, and dependency-free static shell scope.
- Final gates are clean: code-review APPROVE, architect CLEAR, UltraQA 19 static scenarios passed, local lint/focused/full regression passed; CI evidence to be attached after push.

### File List

- `dashboard/static/index.html`
- `tests/dashboard/test_static_shell.py`
- `_bmad-output/implementation-artifacts/89-1-overview-task-list-unavailable-fallback.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Change Log

- 2026-06-15: Created Story 89.1 context/status artifact. Status: ready-for-dev.
- 2026-06-15: Implemented Story 89.1 static Overview/Tasks unavailable fallback with red/green dashboard tests. Status: review.
- 2026-06-15: Reconciled Story 89.1 to done after code-review APPROVE, architect CLEAR, UltraQA pass, and local verification gates.
