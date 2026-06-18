# Story 96.1 — Task detail, event timeline, and trace live-read panels

Status: done

## Scope

Implement metadata/panel-contract wiring only for the Phase 20 Story 96.1 panel families:

- task detail
- task event/transitions timeline
- trace correlation

This story uses the existing Story 95.1 read adapter boundary and remains a pure adapter/presentation-model slice. It does not add actual runtime live API calls.

## Ralplan evidence

- Plan: `.omx/plans/autopilot-story-96-1-task-detail-event-trace-live-read-panels-plan.md`
- Test spec: `.omx/specs/autopilot-story-96-1-task-detail-event-trace-live-read-panels-test-spec.md`
- Architect re-review: `ARCHITECT_VERDICT: APPROVE`, `ARCHITECTURAL_STATUS: CLEAR`
- Critic re-review: `CRITIC_VERDICT: APPROVE`

## Implementation notes

- Add Story 96.1 panel metadata models to `dashboard/live_read_adapter.py`.
- Define exact Story 96.1 route subset:
  - `/v1/tasks/{task_id}`
  - `/v1/tasks/{task_id}/events`
  - `/v1/tasks/{task_id}/transitions`
  - `/v1/trace/{trace_id}`
- Split route input identifiers from row/display identifiers.
- Keep all metadata immutable and fail-closed.
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
- No dependencies.
- No CI/deployment changes.

## Final validation evidence

- `git diff --check` — passed.
- `uv run ruff format --check dashboard/live_read_adapter.py tests/dashboard/test_live_read_panel_contracts.py` — passed.
- `uv run ruff check dashboard/live_read_adapter.py tests/dashboard/test_live_read_panel_contracts.py` — passed.
- `uv run mypy --strict --explicit-package-bases dashboard/live_read_adapter.py tests/dashboard/test_live_read_adapter.py tests/dashboard/test_live_read_panel_contracts.py` — passed.
- Targeted dashboard suite — 88 passed, 2 warnings.
- Full non-slow suite — 4223 passed, 8 skipped, 61 deselected, 30 warnings.
- AI slop cleanup report: `.omx/specs/autopilot-story-96-1-ai-slop-cleaner-report.md`.
- Independent code-reviewer: `019edb81-5aa1-7e21-a8db-98b886972089`, `CODE_REVIEWER_RECOMMENDATION: APPROVE`; one LOW test-sensitivity finding fixed with `FrozenInstanceError` assertion.
- Independent architecture lane: `019ed6d5-fefb-7993-b30d-c08f37404ebb`, `ARCHITECTURAL_STATUS: CLEAR`.
- UltraQA: skipped with evidence because this story adds adapter metadata and tests only, with no runtime live API calls, frontend scripts, backend routes, HTTP clients, user-facing live workflow, mutation/control behavior, dependencies, or CI/deployment changes.

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
