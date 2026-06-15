# Story 88.1: Dashboard static shell and read-only banner

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a single-operator maintainer,
I want a static read-only dashboard shell with a persistent read-only banner and unavailable-state placeholders,
so that Phase 19 can begin with visible non-mutating boundaries before any live dashboard data wiring exists.

## Acceptance Criteria

1. The shell visibly states that the dashboard is read-only and that unsafe or unavailable reads render explicit unavailable states.
2. Navigation exposes only visibility panel placeholders for tasks, sessions, events, traces, replay/lifecycle readiness, health, and Audit and Help.
3. No buttons, forms, menus, links, copy, or visual affordances imply approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.
4. Story 88.1 does not authorize live data wiring, new routes, or dependency selection.
5. Story 88.2 owns route/method allowlist checks, forbidden-call checks, and effect-based no-mutation tests; Story 88.1 must not pull that work forward.
6. The implementation uses static assets/configuration only and explicit unavailable states for panels whose safe reads are not yet approved.
7. No dashboard implementation is performed by this create-story/status slice; this file is ready-for-dev context only.

## Tasks / Subtasks

- [x] Define the future static dashboard shell structure (AC: 1, 2, 6)
  - [x] Keep the repo location intentionally undecided in this create-story slice; the future implementation pass must choose a repo-local location through its own implementation/review gate.
  - [x] Document that fixture or vendored package manifests may exist and must not be treated as an approved dashboard app surface.
  - [x] Record the create-story intake observation, without deciding the future implementation location, that there is no detected first-party frontend/dashboard app surface in the main source tree after excluding vendored, cache, virtualenv, and generated/runtime directories.
- [x] Add a persistent read-only banner in the future shell implementation (AC: 1, 3)
  - [x] Banner copy must state: read-only visibility surface; unsafe reads render unavailable states; mutation/control operations are not available in this dashboard.
  - [x] Banner must not link to approval, retry, cancel, budget override, lifecycle apply/prune, archive mutation, credentialed lifecycle, or production operation controls.
- [x] Add static placeholder navigation for visibility panels only (AC: 2, 6)
  - [x] Include placeholders for tasks, sessions, events, traces, replay/lifecycle readiness, health, and Audit and Help.
  - [x] Use explicit unavailable-state copy for panels whose safe reads are not approved.
  - [x] Do not wire live task/session/event/trace/replay/health data in Story 88.1.
- [x] Preserve the 88.1 / 88.2 boundary (AC: 4, 5)
  - [x] Defer route/method allowlist checks, forbidden-call checks, and effect-based no-mutation tests to future Story 88.2.
  - [x] Do not add backend routes, API schemas, data adapters, cache/projection writes, or dependency selection in Story 88.1.
- [x] Document provenance placeholder behavior (AC: 1, 2, 6)
  - [x] Placeholder panels should reserve space for future source route, timestamp/freshness, trace/event/session references, and confidence copy.
  - [x] Placeholder copy must clearly distinguish unavailable reads from empty successful reads.

## Dev Notes

### Governing FR/NFR

- **FR169**: read-only dashboard scope; the dashboard is an operator visibility surface, not a control surface.
- **FR173**: safe loading, empty, stale, unavailable, unauthorized, and error states.
- **FR174**: this create-story slice does not change runtime behavior.
- **NFR-S27**: read-only by construction.
- **NFR-O24**: provenance-first display.
- **NFR-M20**: reuse existing contracts; new read contracts require separate approval.
- **NFR-R20**: fail-safe visibility through explicit unavailable states.
- **NFR-S28**: lifecycle gate preservation.

### Read-surface basis

Story 88.1 is based on **static assets/configuration only**. It may define shell, navigation, banner, and placeholders, but it must use explicit unavailable states for panels whose safe reads are not yet approved.

The durable rule is: existing safe read surfaces or separately approved future read contract. Story 88.1 does not authorize live data wiring, new routes, or dependency selection.

### Safety guardrails

Only allowlisted read routes/read methods may be reachable in future dashboard work; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.

For Story 88.1 specifically, keep those guardrails at the shell/copy/placeholder level. Story 88.2 owns route/method allowlist checks, forbidden-call checks, and effect-based no-mutation tests.

### Repository structure notes

- repo location is intentionally undecided in this create-story slice.
- The future implementation pass must choose a repo-local location through its own implementation/review gate.
- Current create-story intake records a non-authorizing observation of no detected first-party frontend/dashboard app surface in the main source tree after excluding vendored, cache, virtualenv, and generated/runtime directories; it must not be treated as a settled implementation-location decision.
- fixture or vendored package manifests may exist and must not be treated as an approved dashboard app surface.
- Any frontend stack or dependency addition must be separately justified by the future implementation pass and must not be introduced by this create-story/status-only slice.

### Architecture compliance

- Dashboard shell must remain a separate read-only client concept.
- No dashboard component may import or call registry/event-log write helpers.
- No dashboard route, action, or background refresh may dispatch a job, mutate cache/state, write audit rows, or enqueue lifecycle work.
- Dashboard state is ephemeral client/render state unless a later architecture amendment explicitly approves a read-only server-side projection cache with no writes caused by reads.
- Lifecycle apply/prune and scheduled retention remain outside dashboard scope.

### Testing guidance for the future implementation pass

Story 88.1 implementation should include focused tests or static assertions for shell/banner copy and absence of mutation affordance text in the static UI. It must not implement Story 88.2's route/method allowlist checks, forbidden-call checks, or effect-based no-mutation tests.

### Project Structure Notes

No conflict is currently resolved in this story file. The implementation pass must make the repo-location decision explicitly and record the rationale before adding files.

### References

- `_bmad-output/planning-artifacts/phase-19-prd-amendment.md` — FR169, FR173, FR174, NFR-S27, NFR-O24, NFR-M20, NFR-R20, NFR-S28; planning-only and non-goal boundaries.
- `_bmad-output/planning-artifacts/phase-19-ux-design-specification.md` — persistent read-only banner, information architecture, placeholder/unavailable-state behavior, Audit and Help copy.
- `_bmad-output/planning-artifacts/phase-19-architecture-amendment.md` — safe read surfaces, separate read-only client decision, forbidden surfaces, future verification strategy.
- `_bmad-output/planning-artifacts/phase-19-epics.md` — Epic 88 and Story 88.1/88.2 split.

## Dev Agent Record

### Agent Model Used

GPT-5.4 Codex via Autopilot/Ultragoal.

### Debug Log References

- Red phase: `uv run pytest tests/dashboard/test_static_shell.py` failed 6/6 before `dashboard/static/index.html` existed.
- Green phase: implemented `dashboard/static/index.html`; targeted tests initially failed on two exact banner-copy assertions, then passed after copy refinement.
- Verification: `uv run pytest tests/dashboard/test_static_shell.py` passed 6 tests.
- Verification: `uv run ruff check tests/dashboard/test_static_shell.py` passed.
- Verification: `uv run ruff check .` passed.
- Regression: `uv run pytest -q -m "not slow"` passed 4141 tests, skipped 8, deselected 61 in 143.82s.
- Review cycle 1 fix: strengthened static-shell tests so banner semantics are scoped to `aria-label="Read-only dashboard boundary"` and control terms require banner-local negative context; `uv run pytest tests/dashboard/test_static_shell.py` and `uv run ruff check tests/dashboard/test_static_shell.py` passed.
- Review cycle 2 fix: required every forbidden control term to be present inside the labeled banner; mutation checks and targeted pytest/ruff passed.

### Completion Notes List

- Implemented a dependency-free static dashboard shell at `dashboard/static/index.html`.
- Added a persistent read-only banner with explicit unavailable-read and no mutation/control-operation language.
- Added static placeholder panels for tasks, sessions, events, traces, replay/lifecycle readiness, health, Audit, and Help.
- Each panel carries local unavailable-state language plus source, freshness/timestamp, reference, and confidence placeholders.
- Data panels distinguish an unavailable read from an empty successful read.
- Added standard-library pytest coverage for static shell structure, per-panel provenance placeholders, banner semantics, absence of live API/browser runtime wiring, and absence of control affordance tags.
- Strengthened false-green prevention for banner-scoped read-only semantics and banner-local negative control vocabulary assertions after code review.
- Required every forbidden control term to remain in the labeled banner sentence carrying the exact `affordances are absent` absence phrase.
- Preserved Story 88.1 / Story 88.2 boundary: no backend routes, live data wiring, dependency selection, route/method allowlist checks, forbidden-call checks, or effect-based no-mutation tests were added.

### File List

- `dashboard/static/index.html`
- `tests/dashboard/test_static_shell.py`
- `_bmad-output/implementation-artifacts/88-1-dashboard-static-shell-read-only-banner.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Change Log

- 2026-06-15: Implemented Story 88.1 static read-only dashboard shell and static assertion tests; moved story to review.
- 2026-06-15: Addressed code-review false-green findings by making banner and negative-control assertions banner-scoped.
- 2026-06-15: Addressed follow-up code-review finding by requiring all forbidden control terms to remain present in the labeled banner.
