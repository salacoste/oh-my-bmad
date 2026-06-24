# Story 105.2 — History / Replay Runtime Boundary

## Status

Local implementation, review, architecture, and UltraQA complete — push and remote CI evidence remain pending before final runtime-completion closure.

## Scope implemented

Story 105.2 implements the narrow History / Replay dashboard runtime boundary selected by Story 105.1:

- Exact task-history route: `GET /v1/tasks/{task_id}/history`.
- Exact replay route: `GET /v1/events/replay` with exactly one visible replay target query, `to_sequence` or `to_timestamp`.
- Exact validation route: `GET /v1/events/replay/validate`.
- Visible selector/target sources: `#history-replay-task-id-source`, `#history-replay-target-kind-source`, and `#history-replay-target-value-source`.
- Runtime module: `dashboard/static/history-replay.js`.
- Dashboard DOM targets: status, source routes, task_id, replay target, freshness, authority, history/replay counts, validation status, linked identifiers, and bounded detail copy.

## Tests-first evidence

Initial red evidence:

- Command: `uv run pytest tests/dashboard/test_history_replay_runtime_boundary.py -q`
- Result: expected failures before implementation because `history-replay.js`, the approved script allowlist entry, visible selector/target sources, and bounded runtime metadata targets did not exist.

Final green evidence:

- `uv run pytest tests/dashboard/test_history_replay_runtime_boundary.py -q` → 13 passed, 2 warnings.
- `uv run pytest tests/dashboard/test_history_replay_runtime_boundary.py tests/dashboard/test_health_readiness_runtime_boundary.py tests/dashboard/test_task_detail_runtime_boundary.py tests/dashboard/test_event_timeline_runtime_boundary.py tests/dashboard/test_trace_correlation_runtime_boundary.py tests/dashboard/test_static_shell.py tests/dashboard/test_live_read_lifecycle_panel_contracts.py tests/dashboard/test_live_read_panel_contracts.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_phase20_final_validation.py -q` → 143 passed, 2 warnings.
- `uv run ruff check .` → all checks passed.
- `uv run ruff format --check .` → 590 files already formatted.
- `uv run mypy --strict --explicit-package-bases packages/ services/registry-api services/registry-state` → success, 182 source files.
- `uv run mypy --explicit-package-bases dashboard tests/dashboard` → success, 17 source files.
- Repository check scripts and self-tests passed locally.
- `uv run pytest -m "not slow"` → 4297 passed, 8 skipped, 61 deselected, 25 warnings.
- `node --check dashboard/static/history-replay.js` → passed.
- `git diff --check` → passed.

## Boundary guarantees covered by tests/review

- Only `/v1/tasks/{task_id}/history`, `/v1/events/replay?to_sequence=<visible>`, `/v1/events/replay?to_timestamp=<visible>`, and `/v1/events/replay/validate` are reachable from the history/replay runtime.
- Calls are GET-only and body-free.
- `task_id` is read from visible text only; hidden `data-*`, URL query/hash, storage, polling, and discovery sources are ignored.
- Replay target kind/value are read from visible text only and fail closed unless kind is `to_sequence` or `to_timestamp` and value is non-empty.
- Returned `event_id`, `trace_id`, `replay_id`, `task_id`, and `session_id` values are display/provenance metadata only and do not drive adjacent route fetches.
- Raw replay `state`, task/session rows, history payload values, and validation `field_diffs` values are not rendered.
- Missing returned freshness renders `not returned`; the runtime does not fabricate client timestamps.
- Healthy, empty, partial, stale, invalid JSON, invalid shape, unauthorized, non-2xx, backend-unavailable, and network-failure cases render bounded authoritative or non-authoritative copy.
- `/v1/events/replay/snapshots`, lifecycle readiness, task-list/search/discovery, aggregate/session/digest, generated live data, trace search/list, replay execution, background jobs, snapshot creation, archive metadata changes, mutation/control affordances, and side-effect route families remain unreachable.
- Existing health, task-detail, event/transition, trace, static shell, live-read state/contract, read-only boundary, and final-validation dashboard tests remain green.
- OMX guardrail evidence is recorded in tracked `docs/omx-guardrails.md`: planning/review waits capped at 5 minutes; on timeout attempt one replacement lane spawn; if unavailable, record stale/capacity incident and stop cleanly; do not use `multi_agent_v1.close_agent` recovery; no unbounded waits.

## Changed runtime/test/status files

- `dashboard/static/index.html`
- `dashboard/static/history-replay.js`
- `dashboard/live_read_adapter.py`
- `tests/dashboard/test_history_replay_runtime_boundary.py`
- `tests/dashboard/test_event_timeline_runtime_boundary.py`
- `tests/dashboard/test_health_readiness_runtime_boundary.py`
- `tests/dashboard/test_live_read_contracts.py`
- `tests/dashboard/test_live_read_lifecycle_panel_contracts.py`
- `tests/dashboard/test_live_read_state_contracts.py`
- `tests/dashboard/test_phase20_final_validation.py`
- `tests/dashboard/test_read_only_boundary.py`
- `tests/dashboard/test_static_shell.py`
- `tests/dashboard/test_task_detail_runtime_boundary.py`
- `tests/dashboard/test_trace_correlation_runtime_boundary.py`
- `docs/omx-guardrails.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/105-2-history-replay-runtime-boundary.md`

## Review and QA evidence

- Code review cycle 1: native code-reviewer Maxwell `019ef793-7349-72b0-b6c0-59cd7d36bfb1` returned `REQUEST_CHANGES/BLOCK` for replay validation mismatch fail-open and ignored `.omx` guardrail evidence.
- Rework: validation mismatch now fails closed as non-authoritative; guardrail evidence moved to tracked `docs/omx-guardrails.md`; adapter route-count regression updated.
- Final code review: native code-reviewer Maxwell `019ef793-7349-72b0-b6c0-59cd7d36bfb1` returned `CODE_REVIEW: APPROVE` and `ARCHITECTURAL_STATUS: CLEAR`.
- Architect review: native architect `019ef778-785c-7ce1-81a0-801c82fac3b9` returned `ARCHITECT_REVIEW: APPROVE` and `ARCHITECTURAL_STATUS: CLEAR`.
- UltraQA: `.omx/state/story-105-2-ultraqa-report.md`, `ULTRAQA COMPLETE: Goal met after 1 cycle`.
- Push and remote CI remain pending until commit/push completes.

## Explicit non-authorization preserved

Story 105.2 does not authorize lifecycle readiness, `/v1/events/replay/snapshots`, task-list/search/discovery, aggregate/session/digest, generated live data, replay execution jobs, snapshot creation, broad dashboard live wiring, backend/API route expansion, mutation/control/destructive lifecycle affordances, dependencies, lockfiles, deployment, services, or MCP changes.
