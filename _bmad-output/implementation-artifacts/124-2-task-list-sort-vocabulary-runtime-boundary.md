# Story 124.2 — Task List Sort Vocabulary API-local Runtime Boundary

Status: done  
Phase/Epic: Phase 45 / Epic 124  
Implementation commit: `dceae62f30cacd118b03ec08a8970b642d7ba333`  
Remote CI: `ci` run `28476062586` — https://github.com/salacoste/oh-my-bmad/actions/runs/28476062586

## Scope

Story 124.2 implements the exact API-route-local finite sort vocabulary selected by Story 124.1 for `GET /v1/tasks?sort={task_sort}`.

## Runtime contract implemented

- Accepted standalone raw ASCII sort values are exactly:
  - `updated_at_desc_id_asc`
  - `created_at_desc_id_asc`
- `updated_at_desc_id_asc` orders by `Task.updated_at DESC, Task.id ASC`.
- `created_at_desc_id_asc` orders by `Task.created_at DESC, Task.id ASC`.
- The response returns `route: "GET /v1/tasks?sort={task_sort}"` and `selected_sort` equal to the accepted selector.
- Sort remains mutually exclusive with `status`, `limit`, and `offset`.
- GET request bodies, aliases, encoded keys/values, repeated keys, extra keys, malformed values, status/limit/offset composition, hidden selector shapes, arbitrary sort grammar, and query discovery remain fail-closed.

## Changed runtime/test files

- `services/registry-api/src/registry_api/routes/tasks.py`
- `services/registry-api/src/registry_api/test_app.py`

## Verification evidence

Local validation rerun before commit:

- `uv run ruff check services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py` — passed.
- `uv run mypy services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py` — passed.
- `uv run pytest services/registry-api/src/registry_api/test_app.py -k "sort_selector or sort_filters_are_openapi_visible or sort_composition or request_body" -q` — 7 passed.
- `uv run pytest services/registry-api/src/registry_api/test_app.py::TestGetTasksAggregate -q` — 22 passed.
- `git diff --check -- services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py` — passed.

Additional implementation evidence is recorded in `.omx/ultragoal/story-124-2-evidence.md`:

- Failing-first evidence: before route changes, targeted tests failed as expected for singleton OpenAPI enum and rejected `created_at_desc_id_asc`.
- Final code-review subagent `019f1a4e-2028-7d60-9a55-7eca2d6164a8`: APPROVE, architectural status CLEAR.
- UltraQA subagent `019f1a52-0c3b-7041-a80e-4a23bf38c37c`: PASS.
- Inline adversarial probe accepted exactly two sort queries, rejected 19 invalid query/body/browser-adjacent cases, and verified both orderings.

## Remote CI evidence

- Pushed implementation commit `dceae62f30cacd118b03ec08a8970b642d7ba333` to `origin/main`.
- GitHub Actions `ci` run `28476062586`: success.
- URL: https://github.com/salacoste/oh-my-bmad/actions/runs/28476062586

## Non-goals / deferred surfaces

Story 124.2 does not add browser/dashboard vocabulary controls, status/limit/offset sort composition, arbitrary sort grammar, free-text search, discovery, hidden selectors, automatic traversal, row-derived traversal, replay/lifecycle mutation, services/MCP/dependency changes, lockfile changes, deployment changes, credentials, production operations, or mutation/control behavior.
