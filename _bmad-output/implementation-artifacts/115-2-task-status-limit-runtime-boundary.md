# Story 115.2 — Task Status + Limit Runtime/API Contract Boundary

## Status

Local tests-first implementation, formal code-review, and proportional UltraQA are complete. Push/remote-CI evidence is not recorded in this local implementation lane; Story 115.3/final closure must not run until any required push and remote CI evidence exists.

## Implemented surface

- Exact canonical API route: `GET /v1/tasks?status={task_status}&limit={task_list_limit}`.
- Accepted status selector domain: `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, `failed`.
- Accepted limit selector domain: integer values from 1 through 50 inclusive.
- Query-order policy: canonical-order-only; `status` must appear before `limit`. Reversed `limit={task_list_limit}&status={task_status}` fails closed with 400.
- Response metadata adds `selected_status` and `selected_limit` while preserving the bounded aggregate task summary row shape, order (`updated_at DESC, id ASC`), `returned_count`, `has_more`, and `next_offset: null`.

## Changed runtime/test/docs files

- `services/registry-api/src/registry_api/routes/tasks.py` — adds the status+limit response model, route literal, canonical query validation with ASCII-only integer limit parsing, filtered bounded query behavior, and route-local response metadata.
- `services/registry-api/src/registry_api/test_app.py` — adds red-first coverage for canonical status+limit success, selector domains, reversed-order failure, invalid/repeated/extra/nested selectors, GET-body rejection, and updates legacy invalid-case expectations now that the canonical composition route is authorized.
- `docs/api-contracts.md` — documents the exact Story 115.2 canonical route and deferred/rejected adjacent surfaces.
- `.omx/plans/story-115-2-status-limit-runtime-boundary-plan.md` and `.omx/specs/story-115-2-status-limit-runtime-boundary-test-spec.md` — record RALPLAN query-order and API/dashboard scope decisions.

## Red-first evidence

Before implementation, `uv run pytest services/registry-api/src/registry_api/test_app.py -q -k 'status_limit_composition or request_body_for_unfiltered'` failed as expected with two failures: the new status+limit success/domain tests received 400 from the existing implementation. The existing GET-body rejection test passed.

## Verification evidence

- `uv run pytest services/registry-api/src/registry_api/test_app.py -q` — PASS, 63 passed, 1 warning.
- `uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` — PASS, 6 passed, 2 warnings.
- `uv run ruff check services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py` — PASS.
- `git diff --check` — PASS.
- Code-review rework addressed: ASCII-only limit parsing, sprint-status timestamp refresh, stale RALPLAN consensus placeholder update, empty raw query segment rejection, percent-encoded key/value alias rejection, and overlong integer limit rejection before `int()` conversion.
- Final code-review: `.omx/artifacts/code-review/story-115-2-code-review-final.md` — native code-reviewer `019f0ee6-dcf6-7ee2-8eac-ed445c9eaa73`, `APPROVE` / `CLEAR`.
- UltraQA: `.omx/artifacts/ultraqa/story-115-2-ultraqa-report-rerun.log` — PASS; adversarial API probe accepted only canonical routes, rejected malformed/adversarial selectors, and confirmed no dashboard `/v1/tasks?` wiring.
- Verification logs: `.omx/artifacts/ultragoal/story-115-2/verification.log`, `.omx/artifacts/ultragoal/story-115-2/verification-rerun.log`, `.omx/artifacts/ultragoal/story-115-2/rework-verification.log`, `.omx/artifacts/ultragoal/story-115-2/rework-cycle-2-verification.log`, `.omx/artifacts/ultragoal/story-115-2/rework-cycle-3-verification.log`, and `.omx/artifacts/ultragoal/story-115-2/rework-cycle-4-verification.log`.

## Deferred / fail-closed surfaces

No dashboard JS/HTML/runtime status+limit consumption was added. Browser/dashboard rendering and fail-closed UI copy for the composition route remain deferred to a separate explicit planning gate. Offset/cursor/page traversal, next-page tokens, sorting controls, free-text search, arbitrary filters, saved searches, hidden discovery, row-driven traversal, task detail/digest/history/trace/replay/session traversal, replay execution target calls, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, polling/timers/background refresh, workers, side channels, storage writes, automatic retry, mutation/control calls, dependencies, lockfiles, services/MCP, CI/deployment changes, production credentials, and production operations remain unauthorized.

Generated: 2026-06-28T15:18:53Z
