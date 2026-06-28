# Story 116.3 — Phase 37 / Epic 116 Final Validation Closure

## Status

Done — Phase 37 / Epic 116 closed after Story 116.2 tests-first browser/runtime implementation, independent code-review, UltraQA, push, format repair, and final green GitHub Actions CI evidence.

## Exact implemented dashboard/browser route

- Browser panel: dashboard aggregate-task-list.
- Runtime request: `GET /v1/tasks?status={task_status}&limit={task_list_limit}`.
- Selector source: visible aggregate-task-list status and limit controls only.
- Query spelling/order: canonical `status` first, then `limit`.
- Accepted status values: `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, and `failed`.
- Accepted limit values: ASCII integer values from 1 through 50 inclusive.
- Browser request shape: GET, bodyless, `credentials: "omit"`.

## Implementation commits and remote CI

- Primary implementation commit: `27ddd4e1d05946e178af82bbe79f8f4828045ef1` (`feat(dashboard): consume status limit aggregate tasks`)
- Formatting repair commit / final CI head: `60c6f858431a1049060248f3a9f6e3754e5ac6a2` (`style(dashboard): format status limit tests`)
- Remote branch: `main`
- GitHub Actions workflow: `ci`
- Final CI run: `28332793428`
- CI URL: https://github.com/salacoste/oh-my-bmad/actions/runs/28332793428
- CI conclusion: `success`
- CI jobs passed:
  - Registry-state tests (Postgres service container)
  - PR gate (ruff + mypy + pytest)
- Superseded CI run: `28332747691` failed at `ruff format --check`; `60c6f858431a1049060248f3a9f6e3754e5ac6a2` applied `ruff format` to `tests/dashboard/test_aggregate_task_list_runtime_boundary.py`, and final CI run `28332793428` passed.

## Story 116.1 planning evidence

- `_bmad-output/planning-artifacts/phase-37-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-37-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-37-epics.md`
- `_bmad-output/implementation-artifacts/116-1-task-status-limit-browser-consumption-planning.md`
- Sequential planning consensus: Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR.

## Story 116.2 implementation evidence

- `_bmad-output/implementation-artifacts/116-2-task-status-limit-browser-consumption-runtime-boundary.md`
- Final code review: `.omx/artifacts/code-review/story-116-2-code-review-cycle-2.md` — native code-reviewer `019f0f8f-4155-79d3-8a40-7da6483c3ebf`, `APPROVE` / `CLEAR`.
- UltraQA: `.omx/artifacts/ultraqa/story-116-2-ultraqa-report.md` — PASS, including visible-control click probes for `/v1/tasks?status=pending&limit=50` and `/v1/tasks?status=failed&limit=2`.
- Local pre-push verification before primary implementation push:
  - `node --check dashboard/static/aggregate-task-list.js` → passed.
  - `uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` → `8 passed`.
  - `uv run pytest tests/dashboard -q` → `213 passed`.
  - `uv run pytest services/registry-api/src/registry_api/test_app.py -k 'GetTasksAggregate' -q` → `12 passed, 51 deselected`.
  - `uv run ruff check dashboard/live_read_adapter.py tests/dashboard` → passed.
  - `git diff --check` → passed.
- Local format-repair verification before final push:
  - `uv run ruff format --check .` → `596 files already formatted`.
  - `uv run ruff check dashboard/live_read_adapter.py tests/dashboard` → passed.
  - `uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` → `8 passed`.
  - `git diff --check` → passed.

## Changed implementation surfaces

- `dashboard/static/index.html` — adds visible aggregate-task-list status/limit controls, selected metadata targets, and status+limit boundary copy.
- `dashboard/static/aggregate-task-list.js` — constructs only canonical status+limit routes from visible controls, validates request/response/row authority, and fails closed for invalid selector or response states.
- `dashboard/live_read_adapter.py` — adds approved aggregate status+limit route metadata and identifiers.
- `tests/dashboard/test_aggregate_task_list_runtime_boundary.py` and adjacent dashboard runtime-boundary tests — prove visible-control-only status+limit consumption and preserve other panel allowlists.
- `tests/dashboard/test_live_read_contracts.py`, `tests/dashboard/test_live_read_state_contracts.py`, `tests/dashboard/test_live_read_adapter.py`, `tests/dashboard/test_phase20_final_validation.py`, `tests/dashboard/test_read_only_boundary.py`, and `tests/dashboard/test_static_shell.py` — refresh contract/status checks for the promoted aggregate-task-list boundary.
- `docs/feature-status.md` and `_bmad-output/implementation-artifacts/sprint-status.yaml` — record Story 116.2/116.3 closure evidence and Phase 38 handoff.

## Boundary preserved

Story 116.3 closes only the exact dashboard aggregate-task-list browser consumption/rendering boundary for canonical status+limit reads. It does not introduce or approve offset/cursor/page traversal, next-page token semantics, sorting controls, free-text search, arbitrary discovery, hidden selectors, row-derived traversal, automatic adjacent-route traversal, task detail/digest/history/trace/replay/session traversal, replay execution target selection, lifecycle apply/prune/rollback, broad dashboard wiring beyond the selected aggregate-task-list panel, generated live data, browser-side LLM behavior, polling/timers/background refresh, workers, side channels, storage writes, automatic retry, mutation/control calls, services/MCP changes, dependencies, lockfiles, CI/deployment file changes, production credentials, production operations, or any unplanned adjacent surface.

Generated: 2026-06-28T22:13:22+03:00
