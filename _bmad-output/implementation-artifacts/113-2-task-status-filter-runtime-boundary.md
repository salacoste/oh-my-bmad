# Story 113.2 — Task Status Filter Runtime/API Contract Boundary

## Status

Done — exact status-filter runtime/API boundary completed tests-first for `GET /v1/tasks?status={task_status}`, reviewed, pushed, and proven by green remote CI run `28298018048`. Final Epic 113 closure is recorded in Story 113.3.

## Implemented exact surface

- API/runtime route: `GET /v1/tasks?status={task_status}`.
- Allowed selector domain: exactly one `status` query key whose value is one of `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, or `failed`.
- Existing selector-free route remains supported independently as `GET /v1/tasks`.
- Response route metadata for filtered reads is `GET /v1/tasks?status={task_status}` with visible `selected_status`.
- Filtered rows preserve the bounded aggregate task-list row shape from Story 109.2.

## Boundaries preserved

- No free-text search, arbitrary filters, multi-field query language, pagination/cursor/offset/limit controls, sorting controls, saved searches, hidden discovery, hidden selectors, row-driven traversal, task detail/digest/history/trace/replay/session traversal, replay execution target calls, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, polling/timers/background refresh, workers, side channels, storage writes, automatic retry, mutation/control calls, services/MCP changes, dependencies, lockfiles, CI/deployment file changes, production credentials, or production operations.
- GET bodies are rejected for both unfiltered and filtered reads.
- Repeated status keys, extra query keys, empty/unknown values, uppercase status values, encoded nested parameters, `sort`, `limit`, and `q` selectors fail closed with 400.
- Empty filtered results retain the aggregate list empty-state semantics: `display_state: "empty-list"` and `authority_state: "non-authoritative"`.

## Changed files

- `services/registry-api/src/registry_api/routes/tasks.py` — adds the finite status selector validation, OpenAPI-visible `status` query parameter enum, status-filtered query, and filtered response model metadata.
- `services/registry-api/src/registry_api/test_app.py` — adds tests-first coverage for OpenAPI visibility, accepted finite statuses, exact filtered response metadata, row filtering, rejected extra/repeated/unknown selectors, and GET-body rejection.
- `docs/api-contracts.md` — documents the exact additive status-filter contract and non-goals.
- `docs/feature-status.md` — records Story 113.2 implementation and Story 113.3 final closure evidence.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — marks Story 113.2, Story 113.3, and Epic 113 done with remote CI evidence.

## Verification evidence

- Red test evidence before implementation: `uv run pytest services/registry-api/src/registry_api/test_app.py -k 'GetTasksAggregate and status' -q` — 2 failed because `/v1/tasks?status=...` still returned 400.
- `uv run pytest services/registry-api/src/registry_api/test_app.py -k 'GetTasksAggregate and status' -q` — 2 passed after implementation.
- Review repair red test: `uv run pytest services/registry-api/src/registry_api/test_app.py -k 'openapi_visible' -q` — 1 failed before OpenAPI-visible query declaration because `GET /v1/tasks` had no `parameters` entry.
- Review repair green test: `uv run pytest services/registry-api/src/registry_api/test_app.py -k 'openapi_visible' -q` — 1 passed after declaring the `status` query parameter.
- `uv run pytest services/registry-api/src/registry_api/test_app.py -k 'GetTasksAggregate' -q` — 8 passed.
- `uv run pytest services/registry-api/src/registry_api/test_app.py -q` — 59 passed.
- `uv run ruff check services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py` — passed.
- `uv run ruff format --check services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py` — passed.
- `uv run mypy --strict --explicit-package-bases services/registry-api` — passed.
- `git diff --check` — passed.
- `uv run pytest -m "not slow"` — 4376 passed, 8 skipped, 61 deselected after the OpenAPI repair.
- `just lint` — passed after the OpenAPI repair; secret-hygiene emitted only `scancode-toolkit not installed; license scan skipped` warnings.
- Post-cleanup fast check: `uv run pytest services/registry-api/src/registry_api/test_app.py -k 'GetTasksAggregate' -q`, targeted Ruff format/check, `uv run mypy --strict --explicit-package-bases services/registry-api`, and `git diff --check` — passed.
- Implementation commit: `32fdbaea23816df72bd999b9eb992bab7262ab43` (`feat(dashboard): add task status filter boundary`).
- Remote CI: GitHub Actions `ci` run `28298018048` on `main` — success.
- CI URL: https://github.com/salacoste/oh-my-bmad/actions/runs/28298018048.
- Remote CI jobs passed: Registry-state tests (Postgres service container); PR gate (ruff + mypy + pytest).

## Review/QA note

Initial code-review returned COMMENT/WATCH for missing OpenAPI query-parameter visibility. The implementation was repaired by declaring the `status` query parameter with enum schema visibility while retaining manual fail-closed validation for extra/repeated query keys, and by adding an OpenAPI regression test. Re-review returned APPROVE/CLEAR with no remaining scoped findings. Proportional QA used the full local non-slow/lint gate plus green remote CI because the slice is backend/API-route-local and does not add dashboard/browser runtime wiring.

Generated: 2026-06-27T18:30:00Z
