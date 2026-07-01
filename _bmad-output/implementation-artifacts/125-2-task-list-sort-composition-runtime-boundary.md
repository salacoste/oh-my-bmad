# Story 125.2 — Task List Sort Composition Runtime Boundary

Status: implemented locally after Story 125.2 Autopilot ralplan consensus
Scope: API-local runtime implementation
Context snapshot: `.omx/context/start-story-125-2-planning-implementation-125-3-20260701T142650Z.md`
Deep-interview handoff: `.omx/interviews/story-125-2-125-3-125-4-deep-interview-complete.md`
Ralplan plan: `.omx/plans/story-125-2-api-local-sort-composition-implementation-plan.md`
Test spec: `.omx/specs/story-125-2-api-local-sort-composition-test-spec.md`
Architect review: `.omx/artifacts/ralplan/story-125-2-architect-review.md`
Critic review: `.omx/artifacts/ralplan/story-125-2-critic-review.md`

## Implemented boundary

Story 125.2 implements exactly one API-local full selector composition:

`GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}`

The canonical raw query order is status, then limit, then offset, then sort.
Accepted domains remain finite/bounded:

- `status`: `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, or `failed`.
- `limit`: ASCII integer `1..50`.
- `offset`: ASCII integer `0..2147483647`.
- `sort`: exactly `updated_at_desc_id_asc` or `created_at_desc_id_asc`.

## Runtime semantics

- Filter by selected status.
- Order by selected sort spec with `id ASC` tie-break.
- Apply selected offset after filtering and ordering.
- Fetch `limit + 1` to compute `has_more` and bounded `next_offset`.
- Return selected status, limit, offset, sort, freshness, authority, provenance, request/trace/correlation, returned count, pagination metadata, and bounded task summary rows.

## Fail-closed boundaries

GET bodies, reversed order, repeated keys, empty segments, encoded keys/values, malformed selector values, partial sort composition (`status+sort`, `limit+sort`, `limit+offset+sort`, `status+limit+sort`), extra keys, search/cursor/page/hidden selectors, arbitrary grammar, browser composition, dashboard JavaScript/HTML behavior, generated live data, mutation/control behavior, services/MCP/dependencies/lockfiles/CI/deployment changes, credentials, and production operations remain unauthorized and fail closed.

## Changed files

- `services/registry-api/src/registry_api/routes/tasks.py`
- `services/registry-api/src/registry_api/test_app.py`
- derivative planning/status artifacts and Autopilot evidence files

## Verification evidence

- `uv run pytest services/registry-api/src/registry_api/test_app.py -q` — 74 passed.

Additional lint/status verification is recorded in `.omx/artifacts/ultragoal/story-125-2/ledger.md`.
