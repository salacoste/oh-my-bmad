# Story 91.1 — Task history and replay validation panels

Status: done

## Scope

Implement a static, read-only Task history and replay validation panel in `dashboard/static/index.html` under `#replay-lifecycle-readiness`. The panel renders approved safe read route provenance, passive history/replay validation fields, unavailable/error states, fail-closed archive copy, and explicit Story 91.1/91.2 boundaries.

## Approved read provenance

- `GET /v1/tasks/{task_id}/history`
- `GET /v1/events/replay`
- `GET /v1/events/replay/validate`
- `GET /v1/events/replay/snapshots`

## Passive Story 91.1 fields

- `task_id`
- `history_source`
- `replay_source`
- `validation_status`
- `validation_timestamp`
- `replayed_event_count`
- `live_projection_reference`
- `field_diffs`
- `snapshot_id`
- `snapshot_source`
- `archive_manifest_reference`
- `archive_manifest_digest`
- `retained_hot_segments`
- `archive_segments`
- `problem_details_type`
- `problem_details_status`
- `problem_details_code`
- `freshness`

## Implementation constraints

No backend/API/schema changes, no dependencies, no JavaScript, no live HTTP lookup, no replay execution, no non-read snapshot behavior, no lifecycle execution, no archive/manifest changes, no background jobs, no hidden writes, no cache warming, and no mutation/control affordances. Route names are inert visible provenance only.

## Verification plan

- Red phase: `uv run pytest tests/dashboard/test_static_shell.py -q`
- `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q`
- YAML chronology parse for `backlog -> ready-for-dev -> in-progress -> review`
- `git diff --check`
- `uv run ruff format --check .`
- `uv run ruff check .`
- `uv run pytest -q -m "not slow"`
- independent code-review APPROVE + architect CLEAR
- UltraQA static adversarial scenarios

## Local verification evidence

- Red phase: `uv run pytest tests/dashboard/test_static_shell.py -q` failed 4 Story 91.1 tests against the placeholder Replay / lifecycle readiness panel.
- `uv run pytest tests/dashboard/test_static_shell.py -q` — 34 passed.
- `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q` — 43 passed.
- YAML chronology parse — passed for backlog → ready-for-dev → in-progress before review.
- `git diff --check` — passed.
- `uv run ruff format --check .` — passed after formatting `tests/dashboard/test_static_shell.py`.
- `uv run ruff check .` — passed.
- `uv run pytest -q -m "not slow"` — 4178 passed, 8 skipped, 61 deselected.

Final closure: independent code-review APPROVE, architect CLEAR, UltraQA 7 scenarios passed, implementation CI 27580999500 green, and status-only closure is being reconciled through Ultragoal.
