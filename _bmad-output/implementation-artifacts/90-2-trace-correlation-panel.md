# Story 90.2 — Trace correlation panel

Status: review

## Scope

Implement a static, read-only Trace correlation panel in `dashboard/static/index.html` using `GET /v1/trace/{trace_id}` as inert visible provenance only. The panel renders required metadata slots, route field/query/header contracts, state copy, causality/related-events copy, and explicit no-mutation/no-live boundaries.

## Canonical trace metadata slots

- `trace_source`
- `retrieved_at`
- `linked_event_id`
- `linked_task_id`
- `linked_session_id`
- `parent_event_id`
- `request_id`
- `freshness`

## Implementation constraints

No backend/API/schema changes, no dependencies, no JavaScript, no live HTTP lookup, no trace search/list, no replay/snapshot, no background jobs, no hidden writes, no cache warming, and no mutation/control affordances. `X-Trace-Truncated` and `truncated/paginated result` are passive trace read protocol/state text only.

## Verification plan

- `uv run pytest tests/dashboard/test_static_shell.py -q`
- `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q`
- `git diff --check`
- `uv run ruff format --check .`
- `uv run ruff check .`
- `uv run pytest -q -m "not slow"`
- independent code-review APPROVE + architect CLEAR
- UltraQA static adversarial scenarios

## Local verification evidence

- Red phase: `uv run pytest tests/dashboard/test_static_shell.py -q` failed 6 Story 90.2 tests against the placeholder Trace panel.
- `uv run pytest tests/dashboard/test_static_shell.py -q` — 28 passed.
- `uv run pytest tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py -q` — 38 passed after adding mixed passive/actionable truncate and actionable passive-header regressions.
- `git diff --check` — passed.
- `uv run ruff format --check .` — passed after formatting the new parser tests.
- `uv run ruff check .` — passed.
- `uv run pytest -q -m "not slow"` — 4173 passed, 8 skipped, 61 deselected after the broad truncate exception blocker fix and actionable passive-header regression.

Final `done` remains pending independent code review, UltraQA, pushed CI, and Ultragoal reconciliation.
