# Story 122.2 — Task List Sort API-local Runtime Boundary

Status: done after clean code-review and UltraQA PASS
Scope: API-route-local runtime/tests only
Context snapshot: `.omx/context/for-122-2-implementation-tests-20260629T232503Z.md`
Deep-interview handoff: `.omx/autopilot/deep-interview-story-122-2-task-list-sort-api-local-runtime-boundary.md`
Ralplan plan: `.omx/plans/story-122-2-task-list-sort-api-local-runtime-boundary-plan.md`
Test spec: `.omx/specs/story-122-2-task-list-sort-api-local-runtime-boundary-test-spec.md`

## Implemented boundary

Story 122.2 implements exactly canonical API-local `GET /v1/tasks?sort={task_sort}` with singleton approved value `updated_at_desc_id_asc`.

Runtime behavior:

- Accepts only raw ASCII `sort=updated_at_desc_id_asc` as the full query string.
- Returns `route: "GET /v1/tasks?sort={task_sort}"` and `selected_sort: "updated_at_desc_id_asc"`.
- Preserves the bounded task summary row shape and existing metadata: freshness, display, authority, provenance, request/trace/correlation id, fixed limit, returned_count, `has_more`, `next_offset: null`, and items.
- Uses the existing deterministic ordering `Task.updated_at.desc(), Task.id.asc()`, making the prior implicit first-page ordering explicitly requestable.
- Rejects GET request bodies.
- Rejects aliases, encoded keys/values, Unicode lookalikes, empty/repeated sort keys, nested/JSON/field-direction syntax, extra keys, and all composition with status/limit/offset.

## Non-goals preserved

No browser/dashboard sort control or rendering change was added. Search/discovery, hidden selectors, automatic traversal, row traversal, broader sort vocabulary, sort composition with status/limit/offset, services/MCP/dependency/CI/deployment changes, credentials, generated live data, production operations, and mutation/control behavior remain deferred/fail-closed.

## Changed files

- `services/registry-api/src/registry_api/routes/tasks.py`
- `services/registry-api/src/registry_api/test_app.py`

## Verification evidence

Primary post-rework log: `.omx/artifacts/ultragoal/story-122-2/rework-cycle-1-verification.log`

Fresh local verification after cleanup:

- `uv run pytest services/registry-api/src/registry_api/test_app.py -q` → 72 passed after review rework.
- `uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` → 9 passed after review rework.
- `uv run mypy --strict services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py` → Success, no issues found after review rework.
- `git diff --check` → passed after review rework.

## Cleanup evidence

- `.omx/artifacts/ultragoal/story-122-2/ai-slop-cleaner-report.md` reports no masking fallback-like code, dead code, needless abstraction, or boundary cleanup required in the changed scope.

## Review / QA status

- Code-review cycle 0 returned COMMENT/WATCH and required OpenAPI mutual-exclusion clarity plus dispatcher future-expansion guard.
- Rework added the mutual-exclusion OpenAPI description, a future-expansion dispatcher guard, and a regression assertion for the description.
- Final code-review is clean: `.omx/artifacts/code-review/story-122-2-code-review-final.md` records independent code-reviewer APPROVE and architect CLEAR.
- UltraQA passed: `.omx/artifacts/ultraqa/story-122-2-ultraqa.md` and `.omx/artifacts/ultraqa/story-122-2-ultraqa.log` record baseline tests, dashboard regression, and adversarial ASGI scenarios for normal, malformed, composed, body, OpenAPI, misleading-success, and debris-guard cases.
