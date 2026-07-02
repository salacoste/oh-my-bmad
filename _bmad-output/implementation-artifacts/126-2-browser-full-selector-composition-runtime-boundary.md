# Story 126.2 — Browser Full Selector Composition Runtime Boundary

Status: shipped/green after code-review APPROVE/CLEAR, UltraQA PASS, implementation commit `8d6cfc6`, green remote `ci` run `28555502488`, and green remote `nightly` run `28565399310`
Phase/Epic: Phase 47 / Epic 126  
Generated: 2026-07-01T22:58:27Z

## Summary

Story 126.2 implements the dashboard/browser aggregate-task-list full selector composition boundary for exactly:

`GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}`

The browser uses only visible aggregate-task-list status, limit, offset, and sort controls. The sort control remains finite with exactly `updated_at_desc_id_asc` and `created_at_desc_id_asc`. The API was already implemented by Story 125.2; this story adds no backend/API behavior.

## Implemented behavior

- Primary aggregate task-list runtime route now composes status, limit, offset, and sort in canonical order.
- Fetch remains bodyless GET with `credentials: "omit"`.
- Response validation requires route, selected status/limit/offset/sort, bounded pagination metadata, freshness, display/authority states, provenance, request/trace/correlation identifiers, and bounded rows.
- Rows must match the selected status; malformed rows fail closed.
- Manual previous/next controls preserve the selected sort, remain one explicit user action per read, and require the current selectors to match the last authoritative selector tuple before fetching.
- Invalid, stale, empty-list, unauthorized, backend-unavailable, malformed, and other non-authoritative paths clear navigation and stay fail-closed until an explicit successful authoritative reload.
- The previous standalone sort result subtree and redundant sort-load action were folded into the primary aggregate task-list display to avoid split authoritative task-list displays.

## Deferred / fail-closed surfaces retained

Search/discovery runtime, arbitrary query grammar, hidden selectors, URL/hash/storage/cookie selectors, automatic traversal, infinite scroll, row-driven traversal, broad dashboard rewiring, backend/API changes, generated live data, replay/session/detail/digest/trace traversal, services/MCP changes, dependencies/lockfiles, CI/deployment changes, credentials, production operations, and mutation/control behavior remain unavailable unless separately selected.

## Local verification

- Baseline before runtime edits: `uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` — 13 passed, 2 warnings.
- After implementation: `uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` — 13 passed, 2 warnings.
- Final targeted guard: `uv run pytest tests/dashboard/test_dashboard_wiring_inventory.py tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` — 17 passed, 2 warnings.
- Final dashboard suite: `uv run pytest tests/dashboard -q` — 223 passed, 2 warnings.
- Final static/lint checks: `uv run ruff format --check tests/dashboard`; `uv run ruff check tests/dashboard`; `node --check dashboard/static/aggregate-task-list.js`; `git diff --check` — all passed.

## Review and QA gates

- Code-review: `.omx/artifacts/code-review/story-126-2-code-review.md` — native code-reviewer `019f200b-1c42-7810-90fd-2e16a4f842e5`, Recommendation `APPROVE`, Architectural status `CLEAR`, required changes none.
- UltraQA: `.omx/artifacts/ultraqa/story-126-2-ultraqa.md` — native verifier `019f2011-afb6-7223-89db-3053ca371a90`, Verdict `PASS`, blocking issues none.

## Post-push remote evidence

- Implementation commit: `8d6cfc664b9e85caf42ad5f0fe633ed10913584c` (`8d6cfc6`) — `feat: add browser full selector composition`.
- GitHub Actions `ci` run `28555502488` — completed `success` for head `8d6cfc664b9e85caf42ad5f0fe633ed10913584c`.
- GitHub Actions `nightly` run `28565399310` — completed `success` for head `8d6cfc664b9e85caf42ad5f0fe633ed10913584c`.
