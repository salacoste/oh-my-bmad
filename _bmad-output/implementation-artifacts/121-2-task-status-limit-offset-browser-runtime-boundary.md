# Story 121.2 — Task status + limit + offset browser runtime boundary

## Scope

Story 121.2 wires the dashboard `aggregate-task-list` browser panel to exactly:

`GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}`

The browser boundary is limited to visible aggregate-task-list controls only:

- `aggregate-task-list-status-control` (`select`) with finite lifecycle status values only.
- `aggregate-task-list-limit-control` (`input type="number"`) with integer values 1 through 50 only.
- `aggregate-task-list-offset-control` (`input type="number"`) with integer values 0 through 2147483647 only.
- Visible load / previous / next buttons; no automatic traversal.

## Boundary decisions preserved

- GET remains bodyless.
- Browser fetch uses `credentials: "omit"`.
- Runtime route is composed only from visible status, limit, and offset controls.
- No hidden selectors, selector-free traversal, row-driven traversal, infinite scroll, search, sort, auto-refresh, or broad dashboard live wiring were added.
- Response validation is strict and fail-closed: exact body keys, exact route marker, matching selected status/limit/offset, allowed display/freshness/authority states, bounded pagination metadata, and rows whose `status` matches the selected status.
- Non-healthy, invalid, unauthorized, and unavailable states render non-authoritative fail-closed copy.

## Changed implementation surfaces

- `dashboard/static/aggregate-task-list.js`
  - Adds visible status selector consumption to route composition.
  - Requires selected status to be one of `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, or `failed`.
  - Requires response `selected_status` to match the visible selector and every returned row to match that status.
  - Keeps GET bodyless and `credentials: "omit"`.
- `dashboard/static/index.html`
  - Adds visible status select control and selected-status metadata.
  - Updates panel copy and fixture-readiness rows for the exact status+limit+offset route.
- `dashboard/live_read_adapter.py`
  - Adds the approved exact aggregate route contract while preserving older aggregate route contracts as inert historical fixture/adapter evidence.
- `tests/dashboard/*`
  - Extends aggregate runtime boundary coverage for status+limit+offset route composition, strict response validation, visible-only controls, stale navigation protection, allowed/invalid status domains, and route inventory consistency.

## Local verification evidence

- `uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` → 9 passed.
- `uv run pytest tests/dashboard -q` → 215 passed, 2 warnings.
- `uv run pytest -m 'not integration' -q` → 4346 passed, 8 skipped, 105 deselected, 33 warnings.
- `uv run ruff check dashboard/live_read_adapter.py tests/dashboard/test_aggregate_task_list_runtime_boundary.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_live_read_adapter.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_phase20_final_validation.py tests/dashboard/test_static_shell.py tests/dashboard/test_event_timeline_runtime_boundary.py tests/dashboard/test_health_readiness_runtime_boundary.py tests/dashboard/test_history_replay_runtime_boundary.py tests/dashboard/test_lifecycle_snapshot_runtime_boundary.py tests/dashboard/test_task_detail_runtime_boundary.py tests/dashboard/test_task_log_digest_runtime_boundary.py tests/dashboard/test_trace_correlation_runtime_boundary.py` → All checks passed.
- `git diff --check` → passed.

## Full-suite note

A full `uv run pytest -q` attempt reached 381 passed / 5 skipped before being interrupted after Docker integration journey failures unrelated to this dashboard boundary (`tests/integration/test_journey_1_overnight.py` returned POST `/v1/tasks` 500; `tests/integration/test_journey_3_recovery.py` timed out waiting for the fourth service while three services were healthy). The non-integration suite and dashboard suite are green.

## Review and UltraQA evidence

- Independent code-reviewer: APPROVE, required changes `[]`, after selector-edit fail-closed and typed identifier blockers were fixed.
- Independent architect: CLEAR, required changes none.
- UltraQA: PASS; hostile selector, route, response-validation, stale navigation, status-domain, and fail-closed checks are covered by the dashboard runtime boundary suite.

## Final local verification evidence after review fixes

- `uv run mypy --strict dashboard/live_read_adapter.py tests/dashboard/test_live_read_state_contracts.py` → Success: no issues found in 2 source files.
- `uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py tests/dashboard/test_live_read_adapter.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_static_shell.py -q` → 91 passed.
- `uv run pytest tests/dashboard -q` → 215 passed, 2 warnings.
- `uv run pytest -m 'not integration' -q` → 4346 passed, 8 skipped, 105 deselected, 33 warnings.
- `uv run ruff check ...` on touched Python surfaces → All checks passed.
- `git diff --check` → passed.
