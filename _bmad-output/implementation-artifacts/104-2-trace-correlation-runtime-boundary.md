# Story 104.2 — Trace Correlation Runtime Boundary

## Status

Review — local tests-first implementation complete; independent code-review, QA decision, push, and remote CI are still pending.

## Scope implemented

Story 104.2 implements the narrow Trace correlation dashboard runtime boundary selected by Story 104.1:

- Exact route: `GET /v1/trace/{trace_id}`.
- Visible selector: `trace_id` text in `#trace-correlation-trace-id-source`.
- Runtime module: `dashboard/static/trace-correlation.js`.
- Dashboard DOM targets: trace status, source route, trace_id, freshness, authority, row count, linked identifiers, and bounded detail copy.

## Tests-first evidence

Red test evidence:

- `uv run pytest tests/dashboard/test_trace_correlation_runtime_boundary.py -q`
- Initial result: 9 failures because `trace-correlation.js`, script allowlist entry, visible `trace_id` source, and trace runtime metadata targets did not exist.

Green implementation evidence:

- `uv run pytest tests/dashboard/test_trace_correlation_runtime_boundary.py -q`
- Result: 9 passed.

Related regression evidence:

- `uv run pytest tests/dashboard/test_trace_correlation_runtime_boundary.py tests/dashboard/test_event_timeline_runtime_boundary.py tests/dashboard/test_task_detail_runtime_boundary.py tests/dashboard/test_health_readiness_runtime_boundary.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_static_shell.py tests/dashboard/test_live_read_adapter.py tests/dashboard/test_live_read_contracts.py -q`
- Result: 111 passed.

## Boundary guarantees covered by tests

- Only `/v1/trace/{trace_id}` is constructed by the trace runtime.
- Calls are GET-only and body-free.
- `trace_id` is sourced from visible text only; hidden `data-*`, query/hash, storage, and adjacent identifiers are ignored.
- `event_id`, `task_id`, and `session_id` are returned/display metadata only.
- No trace search/list/discovery route is reachable.
- No history/replay, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, or control route is introduced by the trace runtime.
- Missing trace_id does not fetch.
- Healthy, empty, partial, stale, invalid JSON, invalid shape, mismatched trace_id, unauthorized, and network/backend unavailable cases render bounded authoritative or non-authoritative copy.
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

## Explicit non-authorization preserved

Story 104.2 does not authorize trace search/list/discovery, history/replay, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, broad dashboard live wiring, backend/API route expansion, mutation/control/destructive lifecycle affordances, dependencies, lockfiles, deployment, services, or MCP changes.

## Review/QA gates still pending

- Independent code-review must return APPROVE / CLEAR before marking Story 104.2 done.
- QA must pass or record an explicit skip reason before Autopilot completion.
- Push and remote CI evidence are required before Phase 25 can claim runtime completion/final closure.
