# Story 119.2 — Manual Task-List Pagination Navigation Runtime Boundary

Date: 2026-06-29T05:25:00+03:00
Status: done locally after tests-first implementation, dashboard/API regression validation, code review, and UltraQA verifier evidence
Scope: dashboard aggregate-task-list browser/runtime only

## Implemented surface

Story 119.2 implements exactly one browser/runtime navigation surface selected by Story 119.1:

- visible aggregate-task-list `Previous offset` button;
- visible aggregate-task-list `Next offset` button;
- underlying route remains exactly `GET /v1/tasks?limit={task_list_limit}&offset={task_list_offset}`;
- existing visible limit and offset controls remain the selector source;
- browser fetch shape remains GET, no request body, `credentials: "omit"`, and canonical limit-then-offset query order.

## Runtime behavior

- Manual next is enabled only after an authoritative healthy response with `has_more: true` and a validated numeric `next_offset` matching the visible selector context that produced it.
- Manual previous derives from current visible limit/offset controls and computes exactly `max(current_offset - current_limit, 0)`.
- Selector edits refresh navigation state: previous derives from current visible controls, while stale next metadata fails closed until a fresh authoritative load.
- Invalid/missing/hidden selectors, invalid/non-authoritative responses, unauthorized/backend-unavailable states, malformed JSON, stale selector context, and concurrent/in-flight activation ambiguity fail closed.
- A `loadInFlight` guard disables load/previous/next during a request and prevents rapid repeated activations from issuing overlapping reads.

## Non-authorization statement

Story 119.2 does not add backend/API route changes, automatic pagination traversal, infinite scroll, URL/hash pagination state, local/session storage, cookies, timers, workers, automatic retry, search, sort, status+offset/status+limit+offset composition, hidden selectors, generated selectors, row-derived selectors, row-driven adjacent-route traversal, replay/lifecycle mutation, generated live data, services/MCP/dependencies/CI/deployment changes, production credentials, or production operations.

## Tests-first and regression evidence

Initial targeted Story 119.2 test run failed before implementation because the approved buttons/runtime behavior were absent:

```text
uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q
3 failed, 6 passed
```

After implementation, code-review rework, and concurrency guard rework, local validation passed:

```text
uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q
9 passed, 2 warnings

uv run pytest tests/dashboard -q
215 passed, 2 warnings

uv run pytest services/registry-api/src/registry_api/test_app.py::TestGetTasksAggregate -q
16 passed, 1 warning

uv run ruff check dashboard/static tests/dashboard/test_aggregate_task_list_runtime_boundary.py tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_lifecycle_snapshot_runtime_boundary.py tests/dashboard/test_task_detail_runtime_boundary.py tests/dashboard/test_health_readiness_runtime_boundary.py tests/dashboard/test_event_timeline_runtime_boundary.py tests/dashboard/test_trace_correlation_runtime_boundary.py tests/dashboard/test_history_replay_runtime_boundary.py tests/dashboard/test_task_log_digest_runtime_boundary.py
All checks passed!

node --check dashboard/static/aggregate-task-list.js
git diff --check
```

Regression coverage includes:

- next metadata alone does not auto-traverse;
- manual next performs exactly one explicit canonical GET;
- manual previous performs exactly one explicit canonical GET;
- previous recomputes from edited visible controls;
- previous enables after an offset edit from initial offset `0`;
- next selector-context mismatch fails closed and preserves previous when current visible offset permits it;
- invalid response metadata disables next;
- concurrent rapid previous clicks issue only one additional navigation GET.

## Review and QA evidence

- Story 119.1 planning gate: native Architect agent `019f110c-7d68-7d72-b5fb-5664ae56f36a` returned APPROVE/CLEAR, then native Critic agent `019f110e-901b-7e63-9329-18ab15a9a7b3` returned APPROVE/CLEAR.
- Code-review cycle 1: native `code-reviewer` agent `019f1115-6717-7660-b981-7e92d3c65403` returned REQUEST_CHANGES for stale visible selector/navigation state.
- Code-review cycle 2: native `code-reviewer` agent `019f111b-9655-70f0-8548-33f604e5f904` returned REQUEST_CHANGES for previous disabled-state coupling after selector edits.
- Code-review cycle 3: native `code-reviewer` agent `019f1121-d651-7b90-ad64-691911da5ac8` returned REQUEST_CHANGES for rapid repeated navigation/in-flight ambiguity.
- Final code-review gate: native `code-reviewer` agent `019f112b-0923-77a0-b63d-0da8a718c1b5` returned APPROVE with no findings after the `loadInFlight` guard and concurrent-click regression.
- UltraQA/verifier gate: native `verifier` agent `019f112e-517e-7193-9544-e024cafea8a6` returned PASS, confirming exact route, visible controls, one explicit load at a time, fail-closed selector/metadata behavior, no out-of-scope route/storage/search/sort/status composition, and dashboard-local changed-file scope.

## Completion evidence

Story 119.2 is locally complete after tests-first implementation, local validation, final code-review APPROVE, and UltraQA verifier PASS. Story 119.3 remains pending until the implementation commit is pushed and remote CI evidence exists.
