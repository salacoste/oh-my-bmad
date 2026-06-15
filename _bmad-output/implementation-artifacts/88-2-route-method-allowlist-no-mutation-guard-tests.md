# Story 88.2: Route/method allowlist and no-mutation guard tests

Status: done

## Story

As a single-operator maintainer,
I want static/adversarial route-method and no-mutation boundary guard tests for the read-only dashboard,
so that future dashboard work cannot silently introduce mutating routes, unsafe methods, live API wiring, or mutation-control affordances.

## Acceptance Criteria

1. Tests enumerate the dashboard read boundary and fail if the current static dashboard exposes non-GET or unapproved route/method contexts.
2. Tests prove the current static dashboard has no script, event-handler, form, actionable control, live API, or page-load network context outside the approved static read boundary.
3. Tests cover malformed script/style parsing so unfinished tags cannot hide a mutating API call.
4. Tests enforce exact read-only boundary semantics and prevent positive or mixed mutation-control vocabulary from passing without local negative context.
5. Tests treat approved static/documentation references as safe only after local path canonicalization; traversal escapes and arbitrary local/external routes fail.
6. Story 88.2 does not claim backend runtime effect instrumentation, live data wiring, new routes, dependencies, deployment changes, or mutation/control implementation.

## Tasks / Subtasks

- [x] Add standard-library HTML/static boundary parser tests for `dashboard/static/**/*.html`.
- [x] Define GET-only route allowlist constants and keep optional digest/static references outside the core dashboard route set.
- [x] Assert no scripts, event handlers, forms, actionable controls, live API calls, or unsafe network/page-load contexts are present.
- [x] Add malformed unclosed `script`/`style` finalization coverage.
- [x] Add exact read-only boundary label and per-sentence negative-control vocabulary checks.
- [x] Add safe local href canonicalization coverage, including traversal escape probes.
- [x] Preserve Story 88.1/88.2 boundary: no dashboard HTML edits, no backend routes, no live data wiring, no dependency selection.

## Dev Notes

### Governing FR/NFR

- **FR169**: read-only dashboard scope; visibility only, no mutation/control actions.
- **FR174**: this guard-test story does not change runtime behavior outside the test boundary.
- **NFR-S27**: read-only by construction.
- **NFR-M20**: existing contracts and approved read surfaces first.
- **NFR-R20**: unsafe or unavailable reads fail closed.
- **NFR-S28**: lifecycle gate preservation.

### Read-surface basis

Story 88.2 is static/adversarial route-method/no-mutation boundary guard coverage for the current static dashboard. It is not backend runtime effect proof. Any future live dashboard data wiring still requires a separately approved future read contract or existing safe read surface verification.

### Safety guardrails

Only allowlisted read routes/read methods may be reachable; no mutation routes; no background-job dispatch; no hidden writes; no cache-warming writes/read-side effects; no approval, retry, cancel, budget override, apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled job, credentialed lifecycle, or production operation controls.

## Dev Agent Record

### Agent Model Used

GPT-5.4 Codex via Autopilot/Ultragoal.

### Debug Log References

- Implemented in commit `4993f3e219a9e702e5ded8473354bc4680441ab0` (`4993f3e test(dashboard): guard read-only route boundary`).
- CI evidence: https://github.com/salacoste/oh-my-bmad/actions/runs/27521456863 completed successfully.
- Targeted dashboard regression: `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py` passed 13 tests.
- Full shipped-cycle regression: `uv run pytest -q -m "not slow"` passed 4148 tests, skipped 8, deselected 61.
- Static/adversarial UltraQA harness passed 26 scenarios and 2 negative controls.
- Independent code review returned `APPROVE`; architect review returned `CLEAR`.

### Completion Notes List

- Added `tests/dashboard/test_read_only_boundary.py` as a standard-library static guard suite.
- Covered recursive dashboard HTML discovery, GET-only allowlist vocabulary, script/style finalization, no live API wiring, no actionable controls, safe href canonicalization, and traversal escape failures.
- Preserved static-shell implementation and did not edit `dashboard/static/index.html`.
- Did not add backend routes, live data wiring, dependencies, deployment changes, or mutation/control implementation.

### File List

- `tests/dashboard/test_read_only_boundary.py`
- `_bmad-output/implementation-artifacts/88-2-route-method-allowlist-no-mutation-guard-tests.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

### Change Log

- 2026-06-15: Implemented and shipped Story 88.2 guard tests in commit `4993f3e` with green CI.
- 2026-06-15: Added BMAD story artifact during Epic 88 reconciliation; status set to done based on shipped evidence anchors.
