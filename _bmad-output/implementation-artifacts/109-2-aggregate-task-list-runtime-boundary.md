# Story 109.2 — Aggregate Task List Runtime Boundary

## Status

Done locally — tests-first runtime/API implementation, code review, adversarial QA, syntax check, lint, typecheck, dashboard/registry regression, and full non-slow PR-gate-equivalent validation passed. Push and remote CI are intentionally not claimed here; Story 109.3 remains the final closure story for push/remote-CI evidence consolidation.

## Selected surface

- `GET /v1/tasks` only.

## Scope delivered

- Registry API aggregate task list endpoint:
  - `services/registry-api/src/registry_api/routes/tasks.py`
  - Adds exact `GET /v1/tasks` before the task-create/detail routes.
  - Rejects all query strings and GET request bodies with `400`.
  - Uses a fixed first-page limit of `50`, deterministic ordering by `updated_at DESC, id ASC`, and no offset parameter.
  - Returns route, retrieved/freshness/display/authority state, provenance, request/trace/correlation ids, fixed limit, returned count, `has_more`, `next_offset: null`, and bounded task summary rows.
  - Row shape is intentionally narrow: `task_id`, `status`, `title`, `created_at`, `updated_at`, `state_since`, `actor`, and allowlisted `last_event` metadata (`id`, `type`, `emitted_at`, `trace_id`) only.
- Dashboard aggregate task list panel:
  - `dashboard/static/index.html`
  - Presents the exact route boundary, fixed first-page/no-query/no-body/no-hidden-selector copy, and bounded metadata/row targets.
- Dashboard runtime:
  - `dashboard/static/aggregate-task-list.js`
  - Fetches exactly `/v1/tasks` with `{ method: "GET", credentials: "omit" }` and no body.
  - Validates exact response metadata and exact nested row keys before authoritative rendering.
  - Renders metadata and bounded row text with `textContent`.
  - Fails closed on invalid responses, unavailable backend, unauthorized responses, stale/degraded states, and empty-list non-authoritative reads.
- Live-read contract promotion:
  - `dashboard/live_read_adapter.py`
  - Promotes only `/v1/tasks` to the approved aggregate read family.
  - Keeps session list/detail, digest stream, task-list search/discovery, and broad wiring unavailable or needs-contract.
- Tests:
  - Adds `tests/dashboard/test_aggregate_task_list_runtime_boundary.py`.
  - Extends registry API, live-read, static shell, fixture, and route/script allowlist tests for the exact aggregate route while preserving adjacent fail-closed surfaces.

## Non-authorization / exclusions

This story does not authorize `POST /v1/tasks`, session list/detail reads, digest streaming, task-list search/discovery, hidden selectors, automatic drill-down, browser-side LLM generation, browser-side summarization, generated live data, cache warming, background refresh, polling/timers/retry loops, workers/service workers, storage/cache persistence, cookies, credential include, mutation/control behavior, services/MCP/dependencies/lockfiles/deployment changes, production credentials, or production operations.

## Red-test evidence

Tests were written before implementation. Initial Story 109.2 focused runtime/API suites failed as expected because the aggregate route, dashboard runtime script, approved live-read contract, and status allowlist entries did not yet exist.

Expected failure shape included:

- Missing `GET /v1/tasks` API response model and route behavior.
- Missing `aggregate-task-list.js` script and aggregate panel element ids.
- `/v1/tasks` still classified as unavailable/needs-contract in dashboard live-read contracts.
- Route/script allowlist assertions rejecting aggregate task list runtime wiring.

## Green verification / local CI evidence

- JavaScript syntax:
  - `node --check dashboard/static/aggregate-task-list.js`
  - Result: passed.
- Focused aggregate/dashboard live-read boundary suite:
  - `uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py tests/dashboard/test_live_read_contracts.py -q`
  - Result: `15 passed, 2 warnings in 0.58s`.
- Registry API + dashboard regression suite:
  - `uv run pytest services/registry-api/src/registry_api/test_app.py tests/dashboard -q`
  - Result: `245 passed, 1 warning in 9.55s`.
- Lint/static/type checks:
  - `uv run ruff check` on changed Python/test files.
  - Result: `All checks passed!`.
  - `uv run mypy services/registry-api/src/registry_api/routes/tasks.py dashboard/live_read_adapter.py`
  - Result: `Success: no issues found in 2 source files`.
- Full local PR-gate-equivalent:
  - `uv run pytest -m 'not slow' -q`
  - Result: `4344 passed, 8 skipped, 61 deselected, 26 warnings in 156.52s (0:02:36)`.

## Review and QA evidence

- RALPLAN: `.omx/specs/story-109-2-aggregate-task-list-runtime-boundary-ralplan.md`.
- Test spec: `.omx/specs/story-109-2-aggregate-task-list-runtime-boundary-test-spec.md`.
- Architect review: `.omx/specs/story-109-2-aggregate-task-list-runtime-boundary-architect-review.md` — `APPROVE` / `CLEAR`.
- Critic review: `.omx/specs/story-109-2-aggregate-task-list-runtime-boundary-critic-review.md` — `APPROVE`.
- Code review: `.omx/specs/story-109-2-aggregate-task-list-runtime-boundary-code-review.md` — `APPROVE` after credential-boundary fix.
- Architect recheck: native subagent `019f0199-5211-7303-8837-19c169669805` — `architectural_status: CLEAR`.
- UltraQA: `.omx/ultraqa/story-109-2-aggregate-task-list-runtime-boundary-report.md` — `PASS`.

## Evidence checklist

- Context snapshot: `.omx/context/story-109-2-aggregate-task-list-runtime-boundary-20260626T010929Z.md`.
- Deep interview: `.omx/interviews/story-109-2-aggregate-task-list-runtime-boundary-deep-interview.md`.
- RALPLAN: complete.
- Test spec: complete.
- Red tests: complete.
- Implementation: complete.
- Code review: complete — `APPROVE`.
- UltraQA: complete — `PASS`.
- Local verification: complete.
- Push/remote CI: deferred to Story 109.3; not claimed by Story 109.2.

Generated: 2026-06-26T05:00:00+03:00
