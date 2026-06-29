# Story 120.2 — Task Status + Limit + Offset API-local Runtime Boundary

Date: 2026-06-29T13:50:47Z
Status: done after code-review APPROVE/CLEAR and UltraQA PASS; remote CI pending closure
Scope: API-route-local implementation/tests only

## Implemented boundary

Story 120.2 implements exactly canonical `GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}` in `services/registry-api/src/registry_api/routes/tasks.py`.

The route:

- accepts only raw query order `status` then `limit` then `offset`;
- reuses the existing finite status vocabulary;
- reuses the existing ASCII integer limit domain 1..50;
- reuses the existing ASCII integer offset domain 0..2147483647 with 1-10 raw digits;
- filters by status before offset windowing and selected limit application;
- fetches `limit + 1` rows to derive `has_more` and bounded `next_offset`;
- returns `selected_status`, `selected_limit`, `selected_offset`, route, freshness, authority, provenance, request/trace/correlation id, bounded row count, `has_more`, and `next_offset/null` metadata;
- preserves existing selector-free, status-only, limit-only, status+limit, limit+offset, dashboard status+limit, dashboard limit+offset, and manual previous/next contracts.

## Non-authorization statement

Story 120.2 does not add dashboard/browser consumption of status+limit+offset, status+offset without limit, offset-only reads, automatic traversal, infinite scroll, cursor/page tokens, sorting, free-text search, arbitrary discovery, URL/hash state, local/session storage, cookies, hidden/generated/row-derived selectors, row-driven adjacent-route traversal, replay/lifecycle mutation, generated live data, services/MCP/dependencies/CI/deployment changes, production credentials, or production operations.

## Changed files

- `services/registry-api/src/registry_api/routes/tasks.py` — adds exact status+limit+offset raw-query contract, shared selector parsing helpers, response model, query branch, and response routing.
- `services/registry-api/src/registry_api/test_app.py` — adds tests-first acceptance/domain/body coverage and updates prior closed-route rejection lists for the newly approved exact route.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — marks Story 120.1 done and Story 120.2 in review/in progress.
- `_bmad-output/implementation-artifacts/120-2-task-status-limit-offset-api-local-runtime-boundary.md` — this handoff artifact.

## Tests-first evidence

Initial targeted test run after adding tests and before implementation:

```text
uv run pytest services/registry-api/src/registry_api/test_app.py -k 'status_limit_offset or limit_offset_domains_and_order_are_closed or status_limit_composition_domains_and_order_are_closed or rejects_request_body_for_unfiltered_and_filtered_reads' -q
→ 2 failed, 3 passed
```

Expected failures proved the current route still returned `400` for canonical `status+limit+offset` before implementation.

## Green verification evidence

- `uv run pytest services/registry-api/src/registry_api/test_app.py -k 'status_limit_offset or limit_offset_domains_and_order_are_closed or status_limit_composition_domains_and_order_are_closed or rejects_request_body_for_unfiltered_and_filtered_reads' -q` → `5 passed`.
- `uv run ruff check services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py` → `All checks passed!`.
- `uv run pytest services/registry-api/src/registry_api/test_app.py -q` → `69 passed`.
- post-refactor `uv run pytest services/registry-api/src/registry_api/test_app.py -q` → `69 passed`.
- `git diff --check` → passed.
- `uv run mypy services/registry-api/src/registry_api/routes/tasks.py` → `Success: no issues found in 1 source file`.
- post-refactor `uv run mypy services/registry-api/src/registry_api/routes/tasks.py` → `Success: no issues found in 1 source file`.
- `uv run pytest -m 'not slow' services/registry-api/src/registry_api/test_app.py tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` → `78 passed`.
- `python - <<'PY' ... yaml.safe_load('_bmad-output/implementation-artifacts/sprint-status.yaml') ... PY` → phase/status assertions passed.
- `uv run python -m py_compile services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py` → passed.
- `uv run pytest -m 'not slow' -q` → `4390 passed, 8 skipped, 61 deselected`.

## Review status

Initial native code-review gate returned COMMENT/CLEAR with one LOW docs/status mismatch in `docs/feature-status.md`; first rework updated derivative status. Second native code-review recheck returned REQUEST_CHANGES/WATCH for remaining status drift in `sprint-status.yaml` and `phase-41-epics.md`; second rework aligned current status artifacts. Third native code-review recheck returned REQUEST_CHANGES/CLEAR for timestamp drift, stale feature-status evidence, and ruff-format. Third rework aligned sprint-status timestamps, refreshed stale feature-status evidence, and ran ruff format. Fourth native review returned COMMENT/WATCH for selector branch duplication and raw-query serialization coupling. Fourth rework centralized status/limit/offset selector validation into shared helpers and documented byte-level raw-query matching as the intentional canonical contract. Final native code-review gate `019f13bd-0a29-7853-9bc2-fbd37ed8d457` returned APPROVE/CLEAR with no unresolved findings after selector-helper refactor and raw-query contract documentation.


## Code-review evidence

- Final native code-review: agent `019f13bd-0a29-7853-9bc2-fbd37ed8d457` returned `APPROVE`, architectural status `CLEAR`, unresolved findings `none`, required changes `none`.
- Code-review artifact: `.omx/artifacts/ralplan/story-120-2-code-review.md`.


## UltraQA evidence

- Native verifier agent `019f13c1-c2a8-7860-a2e7-052cd8fe11b5` returned `PASS`, `clean: true`, `required_changes: []`.
- UltraQA artifact: `.omx/artifacts/ultraqa/story-120-2-ultraqa.md`.
