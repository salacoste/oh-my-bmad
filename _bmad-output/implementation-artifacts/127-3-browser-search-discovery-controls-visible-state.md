# Story 127.3 — Browser Search/Discovery Controls from Visible Operator State

Status: implemented locally
Date: 2026-07-02
Autopilot phase: ultragoal → code-review → ultraqa

## Scope

Story 127.3 wires browser-visible aggregate task-list search/discovery controls after the Story 127.2 API-local route. It keeps search explicit, visible-control-only, read-only, and non-traversing.

## Implementation summary

- Added visible dashboard controls for search field, operator, query, and a dedicated search button in `dashboard/static/index.html`.
- Extended `dashboard/static/aggregate-task-list.js` so the search button composes exactly:
  `GET /v1/tasks?field={task_search_field}&op={task_search_operator}&q={task_search_query}&status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}`.
- Browser search uses `fetch(route, { method: "GET", credentials: "omit" })`, never sets a request body, and deliberately avoids `encodeURIComponent`/URL encoding. Query values are accepted only after strict raw visible-control validation.
- Browser-visible search fields are `task_id`, `title`, `actor_id`, `last_event_type`, `updated_at`, and `created_at`. Browser `field=status` is intentionally not exposed because the browser always composes the visible `status=` suffix and must not create duplicate status-selector semantics.
- Search response validation distinguishes browser fetch URL from API response route metadata: response `route` must remain `GET /v1/tasks?field={task_search_field}&op={task_search_operator}&q={task_search_query}` while the fetch URL includes the visible suffixes.
- Rendered metadata includes selected search field/operator/query, selected status/limit/offset/sort, redaction state, row count, freshness, authority, provenance, request/trace/correlation, and bounded pagination metadata.
- Search `has_more`/`next_offset` is display-only. Search responses disable manual previous/next and perform no automatic traversal.
- Hidden/missing/malformed controls, invalid field/operator/query combinations, encoded/plus/whitespace query values, whitespace-mutated field/operator values, response mismatches, and selector edits after authoritative render fail closed before search fetch or before authority is rendered.
- Updated shared dashboard inventory, live-read adapter contracts, static shell route/control guards, feature status, API contracts, sprint status, and Phase 48 epic notes.

## Code review evidence

- Cycle 1 code review found a raw `q` trimming blocker. Rework switched search field/operator/query reads to exact raw strings and added whitespace fail-closed regression coverage.
- Final native `code-reviewer` verdict: APPROVE / CLEAR (`.omx/artifacts/code-review/story-127-3-code-review.md`).

## Verification evidence

- `uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` → 18 passed.
- `uv run pytest tests/dashboard/test_dashboard_wiring_inventory.py tests/dashboard/test_static_shell.py tests/dashboard/test_live_read_adapter.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_phase20_final_validation.py tests/dashboard/test_read_only_boundary.py -q` → 91 passed.
- `uv run pytest tests/dashboard -q` → 228 passed.
- `uv run ruff format --check dashboard tests` → 109 files already formatted.
- `uv run ruff check dashboard tests` → all checks passed.
- `uv run mypy --strict --explicit-package-bases dashboard tests/dashboard` → success, 24 source files.
- `git diff --check` → passed.

## Broader-suite note

- `uv run pytest -q` was attempted. It reached two Docker integration failures unrelated to this browser/dashboard slice before manual interruption after 10m51s: `tests/integration/test_journey_1_overnight.py::test_journey_1_overnight_pr` returned POST `/v1/tasks` 500 inside compose; `tests/integration/test_journey_3_recovery.py::test_journey_3_recovery` timed out waiting for compose health. Cleanup removed leftover `omb-j*` containers/networks.
- `uv run pytest -m 'not integration' -q` was attempted for a broader non-integration signal and was manually interrupted after 6m20s due duration with 433 passed, 5 skipped, 105 deselected, and no failures at interruption.

## Remaining deferred work

- Story 127.4: explicit bounded traversal/infinite-scroll mode remains backlog.
- Story 127.5: search/discovery traversal closure evidence remains backlog.
- Broad dashboard rewiring, destructive lifecycle mutations, object-storage retention, production ops, split deployment/remote Postgres, and DB mTLS remain deferred to later epics.
