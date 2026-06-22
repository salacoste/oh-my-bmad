# Story 102.2 — Task Detail Runtime Boundary Implementation

## Status
Done — implemented, reviewed, UltraQA-verified, and Ultragoal-checkpointed the narrow dashboard live-read runtime boundary for exactly `GET /v1/tasks/{task_id}`.

## Scope delivered
Story 102.2 converts the Task Detail panel from inert provenance-only copy into a single audited browser/runtime read boundary:

- Adds exactly one new executable dashboard runtime file: `dashboard/static/task-detail.js`.
- Mounts exactly one additional external script in `dashboard/static/index.html`: `task-detail.js` with `defer`, alongside the existing `health-readiness.js` script.
- Calls exactly one task-detail route family from task-detail runtime code: `GET /v1/tasks/{task_id}` via `/v1/tasks/<encoded visible task_id>`.
- Uses a visible static DOM text node as the task_id source of truth: `task-detail-task-id-source` = `fixture-task-id`.
- Adds runtime-boundary contract tests in `tests/dashboard/test_task_detail_runtime_boundary.py`.
- Updates health/read-only/static dashboard tests to allow exactly the approved two-script runtime graph while preserving health-only and read-only guardrails.

## Guardrails preserved
Story 102.2 does **not** add backend/API routes, POST/PUT/PATCH/DELETE methods, request bodies, task-list/search/discovery, aggregate/session/digest, event timeline/transitions, trace, history, replay, lifecycle, generated live data, dependencies, lockfiles, CI/deployment changes, services, MCP changes, forms, buttons, inputs, operator controls, mutation affordances, storage/cache writes, polling/timers, beacons, WebSockets, EventSource, XMLHttpRequest, workers, service workers, imports, dynamic imports, preload/modulepreload assets, archive mutation, manifest mutation, background jobs, idempotency writes, cache-warming writes, or side-effectful reads.

## Runtime behavior
The task-detail runtime module:

1. Reads `task_id` from visible DOM text only.
2. Does not read hidden `data-*` attributes, query/hash, storage, task-list/search/discovery, session, aggregate, event, trace, history, replay, or lifecycle sources.
3. Does not call `fetch` when the visible task_id source is missing or blank.
4. Performs one approved `fetch("/v1/tasks/<encoded task_id>", { method: "GET" })` call when the visible task_id is present.
5. Renders valid task detail data as healthy/authoritative.
6. Renders stale, unavailable, backend-unavailable, unauthorized, invalid JSON, unexpected shape, and network failure cases as bounded non-authoritative copy.
7. Keeps visible source route, runtime route, task_id, freshness, authority, and detail metadata in the Task Detail panel.

## Test coverage
Added/updated tests prove:

- runtime script allowlist is exact: `health-readiness.js` + `task-detail.js` only;
- task-detail module graph is closed to one new file with no imports, dynamic imports, workers, service workers, preload/modulepreload assets, polling, storage/cache writes, beacons, WebSockets, EventSource, XMLHttpRequest, query/hash/storage sources, or hidden `data-task-id` source;
- task-detail route construction is limited to `/v1/tasks/` plus encoded visible task_id;
- task-detail runtime method is GET-only and has no request body;
- visible task_id is required and hidden task_id decoys are ignored;
- missing task_id prevents fetch and renders non-authoritative copy;
- healthy/stale/backend-unavailable/invalid/unauthorized/network cases render correctly;
- existing health runtime-boundary source/behavior remains scoped to `GET /v1/health`;
- read-only/static/fixture/live-read regressions stay green with the exact two-script exception.


## Review-cycle repair
Final code review cycle 1 found a fail-open classifier where malformed 2xx JSON objects could render as healthy/authoritative. Ralplan repair added an explicit healthy-shape contract and fresh Architect → Critic approval:

- Healthy authoritative rendering now requires response `task_id` to match the visible requested task_id and `status` to be non-empty.
- `display_state: "healthy"` cannot bypass this validation; malformed healthy-display-state responses render invalid/non-authoritative.
- Regression cases cover `{}`, missing response `task_id`, mismatched response `task_id`, and `display_state: "healthy"` with missing/mismatched `task_id` or blank status.
- Repair verification: `uv run pytest -q tests/dashboard/test_task_detail_runtime_boundary.py` — 9 passed, 2 warnings.
- Full post-repair verification: dashboard Story 102.2 bundle — 98 passed, 2 warnings; `node --check dashboard/static/task-detail.js` passed; `git diff --check` passed.

## Changed-file scope
Implementation-scope files:

- `dashboard/static/task-detail.js`
- `dashboard/static/index.html`
- `tests/dashboard/test_task_detail_runtime_boundary.py`
- `tests/dashboard/test_health_readiness_runtime_boundary.py`
- `tests/dashboard/test_read_only_boundary.py`
- `_bmad-output/implementation-artifacts/102-2-task-detail-runtime-boundary.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

## Verification
Fresh local verification before final review/QA:

- `uv run pytest -q tests/dashboard/test_task_detail_runtime_boundary.py tests/dashboard/test_health_readiness_runtime_boundary.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_static_shell.py tests/dashboard/test_static_fixture_rendering.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_live_read_adapter.py` — 98 passed, 2 warnings.
- `git diff --check` — passed.

## AI slop cleanup report

Scope: Story 102.2 changed files listed above.

Behavior lock:
- Red Story 102.2 tests were added before implementation and failed for missing script/module/metadata/runtime behavior.
- New Story 102.2 tests pass after implementation.
- Existing dashboard read-only/static/fixture/live-read tests pass after the exact two-script runtime exception.

Cleanup plan:
1. Keep the implementation one new runtime file and one route family only.
2. Preserve explicit visible task_id source semantics; no hidden source fallback.
3. Avoid helper modules/imports, polling, storage/cache, service workers, speculative abstractions, or broad compatibility shims.
4. Preserve repeated guardrail language where it prevents future broad live-wiring drift.

Fallback/slop findings:
- No masking fallback, broad compatibility shim, swallowed error, silent default, speculative helper module, hidden HTTP client, extra dependency, or new backend route found.
- Network/parse/authorization failure branches are bounded fail-closed runtime states, not masking fallback slop, because they preserve visible non-authoritative evidence.

Remaining risk:
- Runtime behavior is validated with a lightweight Node/vm harness from pytest instead of a full browser e2e runner. This is acceptable for this narrow boundary but should remain covered by final code review and UltraQA/equivalent runtime-boundary QA.
