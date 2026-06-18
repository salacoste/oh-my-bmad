# Story 96.2 — Replay, history, health, and lifecycle-readiness live-read panels

Status: done

## Scope

Implement metadata/panel-contract wiring only for replay, history, health, and lifecycle-readiness panel families.

This story uses the existing Story 95.1 read adapter boundary and remains a pure adapter/presentation-model slice. It does not add actual runtime live API calls.

## Ralplan evidence

- Plan: `.omx/plans/autopilot-story-96-x-all-remaining-epic-96-live-read-panels-plan.md`
- Test spec: `.omx/specs/autopilot-story-96-x-all-remaining-epic-96-live-read-panels-test-spec.md`
- Architect re-review: `ARCHITECT_VERDICT: APPROVE`, `ARCHITECTURAL_STATUS: CLEAR`
- Critic re-review: `CRITIC_VERDICT: APPROVE`

## Implementation notes

- Add Story 96.2 panel metadata models to `dashboard/live_read_adapter.py`.
- Define exact Story 96.2 route subset:
  - `/v1/tasks/{task_id}/history`
  - `/v1/events/replay`
  - `/v1/events/replay/validate`
  - `/v1/events/replay/snapshots`
  - `/v1/health`
- Split route input identifiers from row/display identifiers.
- Ensure replay/history/lifecycle/health non-healthy states are non-authoritative.
- Keep static dashboard shell inert.

## Non-goals

- No runtime live API calls.
- No HTTP clients.
- No frontend scripts.
- No fetch, XHR, WebSocket, or EventSource.
- No backend route expansion.
- No digest integration.
- No aggregate/session-list live contract.
- No mutation/control behavior.
- No lifecycle apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, or manifest mutation controls.
- No dependencies.
- No CI/deployment changes.

## Validation plan

- `uv run ruff format --check .`
- `git diff --check`
- `uv run ruff check .`
- strict mypy for changed adapter/tests
- targeted dashboard tests
- full non-slow pytest or recorded blocker
- independent code-reviewer APPROVE and architect CLEAR
- UltraQA pass or explicit low-risk skip evidence
- commit, push, and CI green

## Completion evidence

- Implemented `story_96_2_route_patterns()` and `story_96_2_panel_contracts()`
  in `dashboard/live_read_adapter.py`.
- Exact Story 96.2 route subset:
  - `/v1/tasks/{task_id}/history`
  - `/v1/events/replay`
  - `/v1/events/replay/validate`
  - `/v1/events/replay/snapshots`
  - `/v1/health`
- Added `tests/dashboard/test_live_read_lifecycle_panel_contracts.py`.
- Local gates passed:
  - `git diff --check`
  - `uv run ruff format --check dashboard/live_read_adapter.py tests/dashboard/test_live_read_panel_contracts.py tests/dashboard/test_live_read_lifecycle_panel_contracts.py`
  - `uv run ruff check dashboard/live_read_adapter.py tests/dashboard/test_live_read_lifecycle_panel_contracts.py`
  - `uv run mypy --strict --explicit-package-bases dashboard/live_read_adapter.py tests/dashboard/test_live_read_adapter.py tests/dashboard/test_live_read_panel_contracts.py tests/dashboard/test_live_read_lifecycle_panel_contracts.py`
  - targeted dashboard suite: 95 passed
  - full non-slow suite: 4230 passed, 8 skipped, 61 deselected
- Independent code-reviewer: `CODE_REVIEW_RECOMMENDATION: APPROVE`,
  `ARCHITECTURAL_STATUS: CLEAR`; one LOW stale Epic 96 comment was fixed.
- Independent architect: `ARCHITECT_VERDICT: APPROVE`,
  `ARCHITECTURAL_STATUS: CLEAR`.
- UltraQA skipped clean: pure adapter metadata/test slice with no runtime live
  API calls, frontend scripts, backend routes, HTTP clients, user-facing live
  workflow, mutation/control behavior, dependencies, or CI/deployment changes.
