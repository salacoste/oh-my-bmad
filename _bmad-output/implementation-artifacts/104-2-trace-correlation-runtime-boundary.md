# Story 104.2 — Trace Correlation Runtime Boundary

## Status

Done — tests-first implementation, independent code-review, UltraQA, push, and remote CI are complete.

## Scope implemented

Story 104.2 implements the narrow Trace correlation dashboard runtime boundary selected by Story 104.1:

- Exact route: `GET /v1/trace/{trace_id}`.
- Visible selector: `trace_id` text in `#trace-correlation-trace-id-source`.
- Runtime module: `dashboard/static/trace-correlation.js`.
- Dashboard DOM targets: trace status, source route, trace_id, freshness, authority, row count, linked identifiers, and bounded detail copy.

## Tests-first evidence

Initial red evidence:

- Command: `uv run pytest tests/dashboard/test_trace_correlation_runtime_boundary.py -q`
- Result: 9 failures because `trace-correlation.js`, script allowlist entry, visible `trace_id` source, and trace runtime metadata targets did not exist.

Review-cycle regression evidence:

- Command: `uv run pytest tests/dashboard/test_trace_correlation_runtime_boundary.py -q`
- Result: 2 failures proving fabricated `new Date().toISOString()` freshness fallback before repair.
- Command: `uv run pytest tests/dashboard/test_trace_correlation_runtime_boundary.py -q`
- Result: 1 failure proving mismatched trace rows could leak adjacent identifiers before repair.

Final green evidence:

- `uv run pytest tests/dashboard/test_trace_correlation_runtime_boundary.py -q` → 12 passed, 2 warnings.
- `uv run pytest tests/dashboard/test_trace_correlation_runtime_boundary.py tests/dashboard/test_event_timeline_runtime_boundary.py tests/dashboard/test_task_detail_runtime_boundary.py tests/dashboard/test_health_readiness_runtime_boundary.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_static_shell.py tests/dashboard/test_live_read_adapter.py tests/dashboard/test_live_read_contracts.py -q` → 114 passed, 2 warnings.
- `uv run ruff format --check .` → 589 files already formatted.
- `uv run ruff check .` → all checks passed.
- `uv run mypy --strict --explicit-package-bases packages/ services/registry-api services/registry-state` → success, 182 source files.
- Repository check scripts/self-tests, secret hygiene, and `uv run pytest -m "not slow"` passed locally (`4291 passed, 8 skipped, 61 deselected, 28 warnings`).
- `node --check dashboard/static/trace-correlation.js` → passed.
- `git diff --check` → passed.

## Boundary guarantees covered by tests/review

- Only `/v1/trace/{trace_id}` is constructed by the trace runtime.
- Calls are GET-only and body-free.
- `trace_id` is sourced from visible text only; hidden `data-*`, query/hash, storage, and adjacent identifiers are ignored.
- `event_id`, `task_id`, and `session_id` are returned/display metadata only.
- Invalid or mismatched row-level `trace_id` values fail closed and render no linked identifiers.
- No client-side fabricated freshness timestamp is generated; missing `retrieved_at` renders `not returned`.
- No trace search/list/discovery route is reachable.
- No history/replay, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, or control route is introduced by the trace runtime.
- Missing trace_id does not fetch.
- Healthy, empty, partial, stale, invalid JSON, invalid shape, mismatched trace_id, unauthorized, non-2xx, and network/backend unavailable cases render bounded authoritative or non-authoritative copy.
- Existing health, task-detail, event/transition, read-only, static shell, and live-read adapter contract regressions remain green.

## Changed runtime/test files

- `dashboard/static/index.html`
- `dashboard/static/trace-correlation.js`
- `tests/dashboard/test_trace_correlation_runtime_boundary.py`
- `tests/dashboard/test_event_timeline_runtime_boundary.py`
- `tests/dashboard/test_health_readiness_runtime_boundary.py`
- `tests/dashboard/test_task_detail_runtime_boundary.py`
- `tests/dashboard/test_read_only_boundary.py`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/104-2-trace-correlation-runtime-boundary.md`

## Review and QA evidence

- Code review cycle 1: native code-reviewer `019ef1a9-3a0e-75d3-92d3-d3aae4c07955` returned REQUEST CHANGES / BLOCK for fabricated freshness fallback.
- Code review cycle 2: native code-reviewer `019ef1b5-7b9a-7da2-a8fe-657c67b640d1` returned REQUEST CHANGES / BLOCK for mismatched row metadata leakage.
- Final code review: native code-reviewer `019ef1ba-d9cd-7e00-a2b1-dda4eac69d01` returned `CODE_REVIEW: APPROVE/CLEAR`.
- UltraQA: `.omx/state/story-104-2-ultraqa-report.md`, `ULTRAQA COMPLETE: Goal met after 1 cycle`.

## Push and remote CI evidence

- Runtime/story commit: `13bbc37 feat(dashboard): add trace correlation runtime boundary`.
- Formatting CI fix commit: `e0c624c style(tests): format dashboard runtime boundary tests`.
- Remote CI: GitHub Actions `ci` run `28025459660` for `e0c624ce192b1396bb83ed91d2bc1b75233ba0c9` completed successfully.
- URL: `https://github.com/salacoste/oh-my-bmad/actions/runs/28025459660`.

## Explicit non-authorization preserved

Story 104.2 does not authorize trace search/list/discovery, history/replay, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, broad dashboard live wiring, backend/API route expansion, mutation/control/destructive lifecycle affordances, dependencies, lockfiles, deployment, services, or MCP changes.
