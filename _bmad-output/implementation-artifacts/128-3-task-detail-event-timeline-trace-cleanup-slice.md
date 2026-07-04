# Story 128.3 — Task Detail, Event Timeline, and Trace Cleanup Slice

Status: done locally on 2026-07-04.

## Scope
Behavior-preserving read-only cleanup for task inspection panels:
- `dashboard/static/task-detail.js`
- `dashboard/static/event-timeline.js`
- `dashboard/static/trace-correlation.js`
- `tests/dashboard/test_task_detail_runtime_boundary.py`
- `tests/dashboard/test_event_timeline_runtime_boundary.py`
- `tests/dashboard/test_trace_correlation_runtime_boundary.py`

## Cleanup landed
- Extracted module-local visible-source read helpers and read-failure state helpers.
- Routed task-detail missing-source/non-OK rendering through local helper functions.
- Preserved event timeline combined-state precedence and trace freshness `not returned` policy.
- Added focused Story 128.3 locality tests proving cleanup helpers stay module-local and no broad helper/runtime module was introduced.

## Preserved contracts
- Routes remain `GET /v1/tasks/{task_id}`, `GET /v1/tasks/{task_id}/events`, `GET /v1/tasks/{task_id}/transitions`, and `GET /v1/trace/{trace_id}`.
- Fetches remain bodyless GETs from visible text sources only.
- Existing missing/hidden/empty/stale/invalid/unauthorized/backend-unavailable/healthy harness behavior remains unchanged.
- No hidden selectors, row-derived selectors, URL/hash/storage/cookie selectors, side channels, traversal, mutation controls, dependencies, credentials, deployment, or production operations were introduced.

## Verification
- `node --check` for touched runtime files passed in `.omx/artifacts/ultragoal/story-128-remaining/node-check.log`.
- `uv run pytest tests/dashboard/test_task_detail_runtime_boundary.py tests/dashboard/test_event_timeline_runtime_boundary.py tests/dashboard/test_trace_correlation_runtime_boundary.py -q` — 33 passed, 2 pre-existing warnings.
