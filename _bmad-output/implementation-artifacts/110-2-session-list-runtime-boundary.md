# Story 110.2 — Session List Runtime/API Boundary

## Status

Implemented locally on 2026-06-26 after the approved Story 110.2 plan and sequential consensus gate in `.omx/plans/story-110-2-session-list-runtime-boundary-plan.md`.

## Implemented boundary

- Added tests-first `GET /v1/sessions` API coverage in `services/registry-api/src/registry_api/test_app.py`.
- Added route-local backend handler in `services/registry-api/src/registry_api/routes/tasks.py`.
- The backend reads `registry_state.schema.Session` only, rejects query strings and GET bodies, uses fixed server limit `50`, stable sort `last_heartbeat_at DESC NULLS LAST, started_at DESC, id ASC`, and returns the exact bounded response shape.
- Response rows expose only `session_id`, `task_id`, `worker_kind`, `status`, `started_at`, `ended_at`, `last_heartbeat_at`, and backend-derived `heartbeat_state`.
- Raw `worktree_path`, filesystem paths, event payloads, logs, summaries, hrefs/URLs, request/event internals, generated data, and control hints remain omitted.
- `/v1/sessions/{session_id}` and mutation methods remain unapproved/blocked.

## Dashboard boundary

- Promoted `/v1/sessions` to the approved dashboard read contracts in `dashboard/live_read_adapter.py` only for the `session-list` panel family.
- Kept `/v1/sessions/{session_id}` in needs-separate-contract/blocked surfaces and kept digest stream excluded.
- Added `dashboard/static/session-list.js` as one narrow runtime module:
  - one `GET /v1/sessions` fetch;
  - `Accept: application/json`;
  - no body and no credentials override;
  - bounded abort signal;
  - strict content-type, top-level key, row key, metadata, UTC timestamp, `request_id == correlation_id`, display-state/items/authority, fixed limit/sort, and path-leak validation;
  - text-only rendering via `textContent`;
  - fail-closed non-authoritative rendering for non-2xx, unauthorized, malformed, stale/ambiguous, invalid JSON, wrong content type, path leak, over-limit, and network/timeout-like failures.
- Updated `dashboard/static/index.html` and dashboard boundary/static tests to render the approved session-list panel while preserving fail-closed copy for session detail and digest stream.

## Review and QA evidence

- Code review recheck: native `code-reviewer` agent `019f03ae-f491-7d51-be63-a05025a9a143` — `APPROVE`.
- Architect recheck: native `architect` agent `019f03ae-f666-7590-b324-d00aea5c68f0` — `CLEAR`.
- UltraQA Cycle 1: `.omx/ultraqa/story-110-2/scenario-matrix.md` — all listed scenarios PASS after correcting a QA harness command that initially sent JavaScript to Ruff instead of `node --check`.
- AI slop cleanup pass: scoped to Story 110.2 changed files; behavior locked by backend/dashboard tests; removed a new fallback-like display-helper name and one single-use wrapper in `dashboard/static/session-list.js`; no masking fallback slop remains in the new session runtime.

## Local verification evidence

- `uv run pytest services/registry-api/src/registry_api/test_app.py -q` → `54 passed`.
- `uv run pytest tests/dashboard -q` → `199 passed`.
- `uv run pytest tests/dashboard/test_session_list_runtime_boundary.py tests/dashboard/test_live_read_contracts.py -q` → `13 passed`.
- `uv run ruff check services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py dashboard/live_read_adapter.py tests/dashboard` → `All checks passed!`.
- `node --check dashboard/static/session-list.js` → passed.
- `git diff --check` → passed.

## Explicitly deferred / blocked

- No `GET /v1/sessions/{session_id}` implementation or dashboard promotion.
- No session row links, hidden selectors, storage keys, query/hash selectors, row-driven task/session/detail/digest/history/trace/replay fetches, generated summaries, polling, retry loop, cache warming, or browser-side LLM behavior.
- No services/MCP/dependency/lockfile/CI/deployment changes, production credentials, or production operations.
- Story 110.3 remains the final closure story for push/remote-CI evidence.
