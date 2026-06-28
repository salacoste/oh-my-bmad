# Story 114.2 — Task List Limit Runtime/API Contract Boundary

## Status

Local implementation/review/QA complete — tests-first exact API/runtime boundary for `GET /v1/tasks?limit={task_list_limit}` is implemented locally after Story 114.1 Architect/Critic consensus. Code-review returned APPROVE/CLEAR and UltraQA returned PASS. Remote CI/final closure are not claimed here and remain pending Story 114.3.

## Implemented boundary

- Exact new surface: `GET /v1/tasks?limit={task_list_limit}`.
- Selector domain: exactly one `limit` query key with an integer value from 1 through 50 inclusive.
- Response route marker: `GET /v1/tasks?limit={task_list_limit}`.
- Response selected metadata: `selected_limit` plus existing bounded task-list metadata (`limit`, `returned_count`, `has_more`, `next_offset: null`, freshness, authority, provenance, request/trace/correlation id).
- OpenAPI query parameters: `status` and bounded integer/null `limit` only.
- Existing `GET /v1/tasks` and `GET /v1/tasks?status={task_status}` behavior remains independent.

## Non-authorization statement

Story 114.2 does not introduce browser dashboard wiring, offset/cursor/page traversal, next-page token semantics, sorting controls, free-text search, arbitrary filters, saved searches, hidden discovery, automatic row drill-down, task detail/digest/history/trace/replay/session traversal, replay execution target calls, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, polling/timers/background refresh, workers, side channels, storage writes, automatic retry, mutation/control calls, services/MCP changes, dependencies, lockfiles, CI/deployment file changes, production credentials, or production operations.

## Changed files

- `services/registry-api/src/registry_api/routes/tasks.py` — adds limit-selected task-list response model, exact one-key limit selector parsing/fail-closed validation, and bounded integer OpenAPI schema.
- `services/registry-api/src/registry_api/test_app.py` — adds tests-first coverage for OpenAPI visibility, accepted bounded limit values, rejection cases, request-body rejection, and row order/shape preservation.
- `docs/api-contracts.md` — records the exact limit-only API contract and deferred adjacent surfaces.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` / `docs/feature-status.md` — derivative status/evidence updates.

## Verification evidence

- Failing-before-implementation targeted test run: `uv run pytest services/registry-api/src/registry_api/test_app.py::TestGetTasksAggregate -q` failed on missing `limit` OpenAPI parameter and `GET /v1/tasks?limit=...` returning 400.
- Passing final local gates:
  - `uv run ruff check services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py` — passed.
  - `uv run ruff format --check services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py` — passed.
  - `uv run mypy services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py` — passed.
  - `uv run pytest services/registry-api/src/registry_api/test_app.py tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` — 67 passed, 1 warning.
  - `git diff --check` — passed.

## Review and QA evidence

- Code-review cycle 1: `.omx/reviews/phase-35-task-list-limit-runtime-boundary-code-review-cycle-1.md` — COMMENT/WATCH with two findings, both fixed.
- Final code review: `.omx/reviews/phase-35-task-list-limit-runtime-boundary-code-review.md` — code-reviewer subagent `019f0b8a-820a-7490-9c7d-f0ea0f3c72d7`, APPROVE/CLEAR, no findings.
- UltraQA/verifier: `.omx/ultraqa/phase-35-task-list-limit-runtime-boundary-report.md` — verifier subagent `019f0b95-9eef-72d0-b68a-07e57f176629`, PASS, no findings; hostile probes accepted selector-free/status/limit-only reads and rejected invalid/adjoining selectors and GET bodies.

## Pending before final closure

- Story 114.3 final closure artifact/status update after push and remote CI evidence.
- No remote CI, commit, deployment, production credential, or production operation is claimed by Story 114.2.

Generated: 2026-06-28T00:03:43Z
Updated: 2026-06-28T00:20:00Z
