# Story 103.2 — Event Timeline / Transitions Runtime Boundary Implementation

## Status

Done — implemented locally, reviewed, and QA-verified the narrow dashboard live-read runtime boundary for exactly the Event timeline / transitions route family.

## Scope delivered

Story 103.2 converts the Events panel from static provenance copy into a single audited browser/runtime read boundary for exactly one route family:

- `GET /v1/tasks/{task_id}/events`
- `GET /v1/tasks/{task_id}/transitions`

Implementation-scope changes:

- Adds exactly one new executable dashboard runtime file: `dashboard/static/event-timeline.js`.
- Mounts exactly one additional external script in `dashboard/static/index.html`: `event-timeline.js` with `defer`, alongside the already approved `health-readiness.js` and `task-detail.js` scripts.
- Calls exactly two event/transition routes from event timeline runtime code, both built from the visible task_id source.
- Uses visible DOM text as the task_id source of truth: `event-timeline-task-id-source` = `fixture-task-id`.
- Adds runtime-boundary contract tests in `tests/dashboard/test_event_timeline_runtime_boundary.py`.
- Updates existing health/task-detail/read-only/static tests to allow exactly the approved three-script runtime graph while preserving no-control/no-hidden-write guardrails.

## Guardrails preserved

Story 103.2 does **not** add backend/API routes, POST/PUT/PATCH/DELETE methods, request bodies, task-list/search/discovery, aggregate/session/digest, trace runtime wiring, history, replay, lifecycle readiness, generated live data, dependencies, lockfiles, CI/deployment changes, services, MCP changes, forms, buttons, inputs, operator controls, mutation affordances, storage/cache writes, polling/timers, beacons, WebSockets, EventSource, XMLHttpRequest, workers, service workers, imports, dynamic imports, preload/modulepreload assets, archive mutation, manifest mutation, background jobs, idempotency writes, cache-warming writes, or side-effectful reads.

## Selector and semantic-drift boundary

- `task_id` visible text is the only route selector.
- `event_id` is display/provenance metadata only.
- Hidden task_id/event_id decoys are ignored by runtime tests.
- `event_id` cannot drive fetch construction, hidden filtering, trace lookup, history lookup, replay lookup, lifecycle lookup, or discovery.
- Event/transition rows do not render trace/session identifiers or enrich/join through adjacent route families.

## Runtime behavior

The event timeline runtime module:

1. Reads `task_id` from visible DOM text only.
2. Does not read hidden `data-*` attributes, query/hash, storage, task-list/search/discovery, session, aggregate, trace, history, replay, lifecycle, or digest sources.
3. Does not call `fetch` when the visible task_id source is missing or blank.
4. Performs two approved GET calls when task_id is present:
   - `/v1/tasks/<encoded task_id>/events`
   - `/v1/tasks/<encoded task_id>/transitions`
5. Renders valid non-empty event/transition collections as healthy/authoritative.
6. Renders empty, stale, invalid JSON, unexpected shape, mismatched task_id, unauthorized, non-2xx, network failure, and backend-unavailable cases as bounded non-authoritative copy.
7. Keeps visible source routes, runtime routes, task_id, freshness, authority, row counts, and detail metadata in the Events panel.

## Test coverage

Added/updated tests prove:

- runtime script allowlist is exact: `health-readiness.js` + `task-detail.js` + `event-timeline.js` only;
- event timeline module graph is closed to one new file with no imports, dynamic imports, workers, service workers, preload/modulepreload assets, polling, storage/cache writes, beacons, WebSockets, EventSource, XMLHttpRequest, query/hash/storage sources, hidden task_id/event_id selectors, or adjacent semantic markers;
- event/transition route construction is limited to `/v1/tasks/` plus encoded visible task_id plus `/events` or `/transitions`;
- event/transition runtime method is GET-only and has no request body;
- visible task_id is required and hidden task/event decoys are ignored;
- missing task_id prevents fetch and renders non-authoritative copy;
- healthy/empty/stale/invalid/unauthorized/network cases render correctly;
- existing health/task-detail runtime-boundary and dashboard static/read-only/fixture/live-read regressions stay green with the exact three-script exception.

## Changed-file scope

Implementation-scope files:

- `dashboard/static/event-timeline.js`
- `dashboard/static/index.html`
- `tests/dashboard/test_event_timeline_runtime_boundary.py`
- `tests/dashboard/test_health_readiness_runtime_boundary.py`
- `tests/dashboard/test_task_detail_runtime_boundary.py`
- `tests/dashboard/test_read_only_boundary.py`
- `_bmad-output/implementation-artifacts/103-2-event-timeline-transitions-runtime-boundary.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

## Verification

Fresh local verification before final review/QA:

- `uv run pytest -q tests/dashboard/test_event_timeline_runtime_boundary.py` — 9 passed, 2 warnings.
- `uv run pytest -q tests/dashboard/test_event_timeline_runtime_boundary.py tests/dashboard/test_task_detail_runtime_boundary.py tests/dashboard/test_health_readiness_runtime_boundary.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_static_shell.py tests/dashboard/test_static_fixture_rendering.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_live_read_adapter.py tests/dashboard/test_live_read_panel_contracts.py` — 113 passed, 2 warnings.
- `uv run mypy --strict tests/dashboard/test_event_timeline_runtime_boundary.py tests/dashboard/test_health_readiness_runtime_boundary.py tests/dashboard/test_task_detail_runtime_boundary.py tests/dashboard/test_read_only_boundary.py` — passed.
- `uv run ruff check tests/dashboard/test_event_timeline_runtime_boundary.py tests/dashboard/test_health_readiness_runtime_boundary.py tests/dashboard/test_task_detail_runtime_boundary.py tests/dashboard/test_read_only_boundary.py` — passed.
- `uv run ruff format --check tests/dashboard/test_event_timeline_runtime_boundary.py tests/dashboard/test_health_readiness_runtime_boundary.py tests/dashboard/test_task_detail_runtime_boundary.py tests/dashboard/test_read_only_boundary.py` — passed.
- `node --check dashboard/static/event-timeline.js` — passed.
- `git diff --check` — passed.

## Review and QA evidence

- code-reviewer lane `019ef00c-5b7f-7371-abe0-1390ede360f7`: APPROVE, 0 issues.
- architect lane `019ef00c-5cb3-7352-a6c1-531c1178ccf7`: CLEAR.
- QA report: `.omx/specs/story-103-2-ultraqa-report.md`.
- Code review synthesis: `.omx/specs/story-103-2-final-code-review.md`.

## Remaining Phase 24 closure gate

Story 103.2 is locally complete. Phase 24 / Epic 103 final closure remains future Story 103.3 and should cite commit/push and remote CI evidence before closing the epic.
