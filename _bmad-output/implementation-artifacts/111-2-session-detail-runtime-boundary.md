# Story 111.2 — Session Detail Runtime/API Boundary

## Status

Review — tests-first implementation, local validation, independent code-review, and UltraQA complete; final push/remote-CI closure pending.

## Scope implemented

Story 111.2 implements exactly one additive read-only boundary:

- API route: `GET /v1/sessions/{session_id}`.
- Dashboard runtime: `dashboard/static/session-detail.js` builds exactly `/v1/sessions/` plus `encodeURIComponent(visible_session_id)`.
- Static shell: a separate Session detail section reads from visible text `#session-detail-session-id-source` only.
- Live-read contracts: `/v1/sessions/{session_id}` is approved only for the `session-detail` panel family.

## Boundary decisions

- Query strings and GET request bodies are rejected before lookup with HTTP 400.
- Unknown `session_id` returns HTTP 404.
- The backend reads one `Session` row only and returns bounded session display metadata through `SessionSummaryOut`.
- Returned metadata includes route, selected visible session id, retrieved-at timestamp, freshness, authority, provenance, request id, trace id, correlation id, and one bounded item.
- Session-list rows remain inert text; no links, row clicks, data attributes, hidden selectors, prefetch, storage, or automatic drill-down were added.
- Raw `worktree_path`, resource paths, event payloads, logs, summaries, hrefs/URLs, credentials, controls, generated text, and joined task/event data are omitted.
- Dashboard runtime uses one `fetch` call with method `GET`, `Accept: application/json`, no body, no credentials option, no storage, no timers/timeouts/polling/retry loop, no workers, no WebSocket/EventSource/XMLHttpRequest, and no mutation/control behavior.
- Non-healthy states render non-authoritative fail-closed copy.

## Tests-first proof

Initial failing tests were written before implementation and captured in:

- `.omx/tmp/story-111-2-initial-failing-tests.txt`

Initial command:

```bash
uv run pytest services/registry-api/src/registry_api/test_app.py::TestGetSessionDetail tests/dashboard/test_session_detail_runtime_boundary.py -q
```

Initial expected failures proved the route/runtime did not yet exist and the new tests were red before implementation.

## Files changed for runtime/API behavior

- `services/registry-api/src/registry_api/routes/tasks.py` — `SessionDetailResponse` and exact `GET /v1/sessions/{session_id}` handler.
- `services/registry-api/src/registry_api/test_app.py` — backend route tests for success, query/body rejection, unknown id, forbidden methods, and no sensitive fields.
- `dashboard/static/session-detail.js` — bounded browser runtime read/render path.
- `dashboard/static/index.html` — separate inert session-detail panel and static fixture row.
- `dashboard/live_read_adapter.py` — Story 111.2 route/panel contract promotion.
- `tests/dashboard/test_session_detail_runtime_boundary.py` — runtime and DOM boundary tests.
- Dashboard contract tests — route inventory, runtime script allowlists, static fixture rendering, read-only boundary, and phase validation allow exactly this new GET read.


## Code-review evidence

Cycle 1 native code-reviewer `019f053a-0af2-7461-a0d5-331337fa22c3` returned `REQUEST_CHANGES` / `WATCH` for one issue: `AbortSignal.timeout(8000)` violated the literal no-timers guardrail.

Resolution:

- Removed `timeoutSignal()` and `AbortSignal.timeout(8000)` from `dashboard/static/session-detail.js`.
- Removed `signal` from the session-detail `fetch` options.
- Added `AbortSignal.timeout` to the session-detail runtime forbidden marker test.
- Updated runtime expectations to assert `hasSignal: False`.

Cycle 2 re-review by the same native code-reviewer returned `APPROVE` / `CLEAR`; artifact: `.omx/reviews/story-111-2-code-review-cycle-2.md`.


## UltraQA evidence

Native verifier agent `019f0546-3178-7f72-b8d7-c6ac7423f934` returned `PASS` with `clean=true` and no blockers. Artifact: `.omx/ultraqa/story-111-2-session-detail-runtime-boundary-report.md`.

Adversarial coverage included exact route/API rejection behavior, visible-source-only runtime selection, encoded path segment, no session-list drill-down, no hidden selectors/storage/hash/query/cookie/generated prompts/data-attribute inputs, no body/credentials/signal/timers/timeouts/workers/WebSocket/EventSource/XMLHttpRequest/sendBeacon/innerHTML/polling/retry/mutation/control, bounded Session-table metadata only, fail-closed non-authoritative states, and deferred-surface guard preservation.

## Local validation evidence

Focused red-to-green target:

```bash
uv run pytest services/registry-api/src/registry_api/test_app.py::TestGetSessionDetail tests/dashboard/test_session_detail_runtime_boundary.py -q
# 6 passed, 1 warning in 0.50s
```

Affected contract suites:

```bash
uv run pytest services/registry-api/src/registry_api/test_app.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_live_read_adapter.py tests/dashboard/test_phase20_final_validation.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_session_list_runtime_boundary.py tests/dashboard/test_session_detail_runtime_boundary.py tests/dashboard/test_static_shell.py -q
# 150 passed, 1 warning in 3.17s
```

Post-cleanup focused/static suites:

```bash
uv run pytest services/registry-api/src/registry_api/test_app.py::TestGetSessionDetail tests/dashboard/test_session_detail_runtime_boundary.py tests/dashboard/test_session_list_runtime_boundary.py tests/dashboard/test_static_shell.py tests/dashboard/test_static_fixture_rendering.py -q
# 59 passed, 1 warning in 1.49s

uv run pytest tests/dashboard/test_live_read_contracts.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_live_read_adapter.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_phase20_final_validation.py -q
# 49 passed, 2 warnings in 0.27s
```

Full local API/dashboard suites:

```bash
uv run pytest services/registry-api/src/registry_api/test_app.py -q
# 56 passed, 1 warning in 1.56s

uv run pytest tests/dashboard -q
# 203 passed, 2 warnings in 8.66s
```

Syntax/static gates:

```bash
node --check dashboard/static/session-detail.js && node --check dashboard/static/session-list.js
# pass

python -m py_compile dashboard/live_read_adapter.py services/registry-api/src/registry_api/routes/tasks.py
# pass

uv run ruff check dashboard/live_read_adapter.py services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py tests/dashboard/test_session_detail_runtime_boundary.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_static_shell.py tests/dashboard/test_session_list_runtime_boundary.py tests/dashboard/test_static_fixture_rendering.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_phase20_final_validation.py tests/dashboard/test_live_read_adapter.py
# All checks passed!

uv run ruff format --check dashboard/live_read_adapter.py services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py tests/dashboard/test_session_detail_runtime_boundary.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_static_shell.py tests/dashboard/test_session_list_runtime_boundary.py tests/dashboard/test_static_fixture_rendering.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_phase20_final_validation.py tests/dashboard/test_live_read_adapter.py
# 13 files already formatted

uv run mypy services/registry-api/src/registry_api/routes/tasks.py dashboard/live_read_adapter.py
# Success: no issues found in 2 source files

git diff --check
# pass
```

## Deferred / not authorized

Story 111.2 does not authorize session mutation/search/discovery, automatic session-list row drill-down, task/detail/digest/history/trace/replay traversal, digest streaming, broad dashboard wiring, generated live data, browser-side LLM generation/summarization, cache warming/background refresh, mutation/control behavior, services/MCP/dependencies/CI workflow changes, deployment changes, production credentials, or production operations.

## Next gates

- Independent code-review returned `APPROVE` with architectural status `CLEAR`.
- UltraQA passed adversarial runtime/API boundary scenarios.
- Story 111.3 may close Epic 111 only after push plus remote CI evidence.

Generated: 2026-06-26T18:35:54Z
