# Story 117.2 — Task List Pagination Runtime/API Boundary

Date: 2026-06-28T21:05:00Z
Status: done locally after code-review APPROVE/CLEAR and UltraQA PASS; final closure/push/remote CI deferred to Story 117.3
Scope: registry-api API-local tests-first implementation

## Implemented route boundary

Story 117.2 implements exactly canonical:

`GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`

Allowed selectors:

- `limit`: one ASCII integer from 1 through 50 inclusive.
- `offset`: one ASCII non-negative integer from 0 through 2147483647 inclusive, with raw query spelling capped to 1-10 ASCII digits before integer conversion.
- Canonical raw query order is `limit` first, then `offset`.

## Response metadata

The limit+offset response includes:

- `route`
- `selected_limit`
- `selected_offset`
- `retrieved_at`
- `freshness_state`
- `display_state`
- `authority_state`
- `provenance`
- `request_id`
- `trace_id`
- `correlation_id`
- `limit`
- `returned_count`
- `has_more`
- `next_offset`
- `items`

`has_more` and `next_offset` are bounded to the approved API surface: `next_offset` is emitted only when another page is reachable inside the accepted offset domain; otherwise `has_more=false` and `next_offset=null`.

## Fail-closed boundary

The implementation rejects GET request bodies, reversed query order, extra/repeated keys, empty raw query segments, empty values, negative offsets, fractional/non-integer offsets, encoded ASCII digits, Unicode digits, nested params, offset values above `2147483647`, overlong offsets, status+offset composition, status+limit+offset composition, cursor/page/sort/search keys, and hidden selector shapes.

## Preserved/deferred surfaces

Selector-free `GET /v1/tasks`, status-only, limit-only, status+limit, and dashboard aggregate-task-list status+limit contracts remain independently preserved. Browser pagination controls, automatic traversal, infinite scroll, sorting, free-text search, arbitrary discovery, row-derived traversal, replay/lifecycle mutation, generated live data, services/MCP/dependencies/CI/deployment changes, production credentials, and production operations remain deferred/fail-closed.

## Local verification evidence

- Red test evidence before implementation: `uv run pytest services/registry-api/src/registry_api/test_app.py::TestGetTasksAggregate -q` failed with 5 new Story 117.2 failures and 11 passes.
- Green targeted evidence after implementation: `uv run pytest services/registry-api/src/registry_api/test_app.py::TestGetTasksAggregate -q` → `16 passed, 1 warning`.

Additional verification/code-review/UltraQA evidence:

- Full affected regression after rework: `uv run pytest services/registry-api/src/registry_api/test_app.py tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` → `75 passed, 1 warning`.
- Typecheck: `uv run mypy services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py` → `Success: no issues found in 2 source files`.
- Lint/diff: `uv run ruff check services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py && git diff --check` → `All checks passed`.
- Code-review: code-reviewer `019f1002-5a3b-7b92-b454-d1928c593dd8` returned `APPROVE`; architect `019f1002-5be2-72d2-8c00-566eb75d33d4` returned `CLEAR`.
- UltraQA: `.omx/artifacts/ultraqa/story-117-2-report.md` records PASS after baseline verification and inline hostile ASGI harness (`accepted=3 rejected=10 body=1 cleanup=tempdir-removed`).
