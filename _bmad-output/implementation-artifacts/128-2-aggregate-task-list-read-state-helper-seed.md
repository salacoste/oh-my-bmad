# Story 128.2 — Aggregate Task-list Read-State Helper Seed

Status: done locally on 2026-07-03.

## Scope
Story 128.2 seeds aggregate task-list-local read-state/fail-closed helpers in `dashboard/static/aggregate-task-list.js` and proves the refactor with the existing focused aggregate task-list runtime boundary test file.

## Changed runtime/test files
- `dashboard/static/aggregate-task-list.js`
  - Adds local constants for duplicated invalid selector/fail-closed copy.
  - Adds `selectorReadValue` and `selectedWindowReadState` helper seed.
  - Routes `renderClosed` and `renderSearchClosed` through the shared helper while preserving exact rendered state/copy, route patterns, runtime route strings, navigation disabling, traversal clearing, and search redaction behavior.
- `tests/dashboard/test_aggregate_task_list_runtime_boundary.py`
  - Adds Story 128.2 static locality coverage that confirms the helper seed remains aggregate-task-list-local and does not add a runtime module or broaden route/search selectors.

## Preserved contracts
- No new backend/API route, dashboard module, dependency, credentials, deployment, production operation, storage, timer, worker, observer, prefetch, cache warming, retry loop, URL/hash/cookie selector, hidden selector, row-derived selector, mutation, automatic traversal, or infinite scroll.
- Existing selector-free/status/limit/offset/sort/search/traversal contracts remain unchanged.
- Existing route allowlist remains exactly `/v1/tasks`; fetch remains bodyless GET with `credentials: "omit"`.

## Gate evidence
- Deep-interview handoff: `.omx/artifacts/deep-interview/story-128-2-handoff.md`.
- Ralplan plan/test spec: `.omx/plans/story-128-2-aggregate-task-list-read-state-helper-seed-plan.md`, `.omx/specs/story-128-2-aggregate-task-list-read-state-helper-seed-test-spec.md`.
- Architect: `.omx/artifacts/ralplan/story-128-2-architect-review-cycle-2.md` — APPROVE/CLEAR.
- Critic: `.omx/artifacts/ralplan/story-128-2-critic-review.md` — APPROVE/CLEAR.

## Verification evidence
Recorded at `.omx/artifacts/ultragoal/story-128-2/rework-cycle-2-verification.log`:
- `node --check dashboard/static/aggregate-task-list.js` — passed.
- `uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` — 30 passed, 2 pre-existing warnings.
- `uv run ruff check tests/dashboard/test_aggregate_task_list_runtime_boundary.py` — passed.
- `git diff --check` — passed.

## Deferred / not authorized
- Stories 128.3-128.7 cleanup slices remain pending.
- Remote CI/shipped evidence remains pending until push.
- Destructive lifecycle mutations, object-storage retention jobs, deployment/credentials/production operations, split deployment/remote Postgres scaling, and DB connection mTLS remain deferred to their future stories.
