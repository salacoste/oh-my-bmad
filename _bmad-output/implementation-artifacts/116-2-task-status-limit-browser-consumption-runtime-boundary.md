# Story 116.2 — Task Status + Limit Browser Consumption Runtime Boundary

Date: 2026-06-28T18:56:00Z
Status: implemented locally with code-review APPROVE/CLEAR and UltraQA PASS; push / remote CI / final closure pending Story 116.3
Scope: dashboard/browser runtime boundary + tests/evidence only

## Implemented surface

- Exact browser/runtime surface: dashboard aggregate-task-list panel consumption/rendering of canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}`.
- Selector source: visible aggregate-task-list controls only:
  - `aggregate-task-list-status-control`
  - `aggregate-task-list-limit-control`
  - `aggregate-task-list-load`
- Browser request shape: GET, bodyless, `credentials: "omit"`, canonical status-before-limit query order.
- Accepted status values: `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed`.
- Accepted limit values: ASCII integer strings `1` through `50` inclusive.

## Mode decision

Story 116.2 promotes the aggregate-task-list browser runtime read to the exact status+limit composition route. The browser module no longer issues the selector-free aggregate fetch. Backend/API selector-free, status-only, limit-only, and status+limit route contracts remain independently green.

## Changed runtime files

- `dashboard/static/index.html`
  - Adds visible aggregate-task-list status and limit controls.
  - Adds selected-status and selected-limit metadata targets.
  - Updates aggregate panel copy to the canonical status+limit route and visible-control boundary.
  - Adds fixture-readiness metadata for the new approved aggregate status+limit route.
- `dashboard/static/aggregate-task-list.js`
  - Reads selectors only from visible aggregate-task-list controls.
  - Validates finite status and bounded ASCII limit before fetch.
  - Fetches only `/v1/tasks?status=<status>&limit=<limit>` with GET/no body/credentials omit.
  - Validates route, selected status, selected limit, freshness, healthy authority, provenance, correlation/request/trace id, returned_count, has_more, next_offset, exact top-level keys, exact row keys, row status matching the visible selector, and row count before authoritative render.
  - Fails closed for invalid controls, malformed/over-broad responses, mismatches, non-2xx, malformed JSON, and network errors.
- `dashboard/live_read_adapter.py`
  - Adds the canonical status+limit route to the approved aggregate read inventory and panel contract metadata.

## Changed test files

- `tests/dashboard/test_aggregate_task_list_runtime_boundary.py`
- `tests/dashboard/test_live_read_contracts.py`
- `tests/dashboard/test_live_read_state_contracts.py`
- `tests/dashboard/test_live_read_adapter.py`
- `tests/dashboard/test_phase20_final_validation.py`
- `tests/dashboard/test_read_only_boundary.py`
- `tests/dashboard/test_static_shell.py`
- Existing panel runtime control-allowlist tests updated for the newly authorized visible aggregate controls:
  - `tests/dashboard/test_event_timeline_runtime_boundary.py`
  - `tests/dashboard/test_health_readiness_runtime_boundary.py`
  - `tests/dashboard/test_history_replay_runtime_boundary.py`
  - `tests/dashboard/test_lifecycle_snapshot_runtime_boundary.py`
  - `tests/dashboard/test_task_detail_runtime_boundary.py`
  - `tests/dashboard/test_task_log_digest_runtime_boundary.py`
  - `tests/dashboard/test_trace_correlation_runtime_boundary.py`

## Verification evidence

- `node --check dashboard/static/aggregate-task-list.js` — passed.
- `uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` — 8 passed.
- `uv run pytest tests/dashboard -q` — 213 passed.
- `uv run pytest services/registry-api/src/registry_api/test_app.py -k 'GetTasksAggregate' -q` — 12 passed, 51 deselected.
- `uv run ruff check dashboard/live_read_adapter.py tests/dashboard` — All checks passed.
- `git diff --check` — passed.

## Deferred surfaces still fail-closed

No backend/API route changes, dependencies, lockfiles, CI/deployment changes, services/MCP changes, production credentials, URL/hash/query-state persistence, cookies, local/session storage, generated selectors, hidden inputs, row-derived selectors, polling/timers/background refresh, workers, automatic retry, adjacent route traversal, pagination traversal, sorting controls, free-text search, arbitrary query language, replay execution target selection, lifecycle mutation behavior, or broad dashboard mode switching were introduced.

## Code-review and UltraQA evidence

- Code-review cycle 0 (`019f0f80-c529-7082-9d27-a8ef87712b5a`) returned REQUEST_CHANGES/BLOCK for row status mismatch leakage. Fixed by requiring every row `status` to equal the visible selected status and adding `row-status-mismatch` regression coverage.
- Code-review cycle 1 (`019f0f86-36dc-7463-8cc1-86399bb8b687`) returned REQUEST_CHANGES/BLOCK for healthy/non-authoritative mismatch and test harness default drift from real visible controls. Fixed by requiring healthy responses to be authoritative, adding `healthy-non-authoritative` regression coverage, and deriving runtime harness defaults from the HTML controls.
- Code-review cycle 2 (`019f0f8f-4155-79d3-8a40-7da6483c3ebf`) returned APPROVE/CLEAR with no blocking findings. Artifact: `.omx/artifacts/code-review/story-116-2-code-review-cycle-2.md`.
- UltraQA (`019f0f92-cb87-7af0-9e76-77891cfaed80`) returned PASS with no blocking findings and an additional visible-control click probe for `/v1/tasks?status=pending&limit=50` and `/v1/tasks?status=failed&limit=2`. Artifact: `.omx/artifacts/ultraqa/story-116-2-ultraqa-report.md`.
