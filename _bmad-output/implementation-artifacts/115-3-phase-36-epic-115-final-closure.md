# Story 115.3 — Phase 36 / Epic 115 Final Validation Closure

## Status

Done — Phase 36 / Epic 115 closed after Story 115.2 tests-first implementation, independent code-review, UltraQA, push, remote CI repair, and final green remote CI evidence.

## Exact implemented route

- `GET /v1/tasks?status={task_status}&limit={task_list_limit}`
- Query spelling: canonical-order-only, with exactly one `status` selector followed by exactly one `limit` selector.
- Accepted status values: `pending`, `planning`, `plan_ready`, `executing`, `blocked`, `completed`, `stopped`, and `failed`.
- Accepted limit values: ASCII integer values from 1 through 50 inclusive.

## Implementation commits and remote CI

- Primary implementation commit: `e673bb24dfa9d04f409af9ffdf3e3e7308f824d2` (`feat(dashboard): add task status limit boundary`)
- Formatting repair commit: `59795d6` (`style(registry-api): format status limit tests`)
- Final CI head commit: `bb7c338e2200e67a2e114a60e5c43b333b51fc7d` (`chore(ci): verify story 115 status limit boundary`)
- Remote branch: `main`
- GitHub Actions workflow: `ci`
- Final CI run: `28329475903`
- CI URL: https://github.com/salacoste/oh-my-bmad/actions/runs/28329475903
- CI conclusion: `success`
- CI jobs passed:
  - Registry-state tests (Postgres service container)
  - PR gate (ruff + mypy + pytest)
- Superseded CI run: `28329325944` failed at `ruff format --check`; `59795d6` applied `ruff format` to `services/registry-api/src/registry_api/test_app.py`, and the final head `bb7c338` passed remote CI.

## Story 115.1 planning evidence

- `_bmad-output/planning-artifacts/phase-36-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-36-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-36-epics.md`
- `_bmad-output/implementation-artifacts/115-1-task-status-limit-route-selection-planning.md`
- Sequential planning consensus: Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR.

## Story 115.2 implementation evidence

- `_bmad-output/implementation-artifacts/115-2-task-status-limit-runtime-boundary.md`
- Red-first proof: focused status+limit tests initially failed with 400 from the pre-existing implementation before the canonical composition route was implemented.
- Final code review: `.omx/artifacts/code-review/story-115-2-code-review-final.md` — native code-reviewer `019f0ee6-dcf6-7ee2-8eac-ed445c9eaa73`, `APPROVE` / `CLEAR`.
- UltraQA: `.omx/artifacts/ultraqa/story-115-2-ultraqa-report-rerun.log` — PASS; adversarial API probe accepted only canonical routes, rejected malformed/adversarial selectors, and confirmed no dashboard `/v1/tasks?` wiring.
- Local focused verification before push:
  - `uv run pytest services/registry-api/src/registry_api/test_app.py -q` → `63 passed, 1 warning`.
  - `uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` → `6 passed, 2 warnings`.
  - `uv run ruff check services/registry-api/src/registry_api/routes/tasks.py services/registry-api/src/registry_api/test_app.py` → passed.
  - `git diff --check` → passed.
- Local repair verification before final push:
  - `uv run ruff format --check` → `596 files already formatted`.
  - Sentinel sample for last-commit checks → `4 passed, 2 warnings`.
  - `uv run pytest services/registry-api/src/registry_api/test_app.py -q` → `63 passed, 1 warning`.
  - `uv run mypy --strict --explicit-package-bases packages/ services/registry-api services/registry-state` → passed.
  - CI static scripts and tracked-file secret hygiene → passed; local secret hygiene emitted only `scancode-toolkit not installed; license scan skipped` warnings.

## Changed implementation surfaces

- `services/registry-api/src/registry_api/routes/tasks.py` — added status+limit response model, exact route marker, canonical raw-query validation, ASCII-only bounded limit parsing, filtered bounded query behavior, and selected-status/selected-limit response metadata.
- `services/registry-api/src/registry_api/test_app.py` — added status+limit success/domain/order/fail-closed tests and GET-body rejection coverage for the composition route.
- `docs/api-contracts.md` — documents the exact canonical status+limit route and explicitly rejects reversed query order, extra/repeated selectors, GET bodies, traversal, search, sorting, dashboard consumption, and broad wiring.
- `docs/feature-status.md`, `_bmad-output/implementation-artifacts/sprint-status.yaml`, Phase 36 planning artifacts, and Story 115 implementation artifacts — updated planning, implementation, CI, review, QA, and closure evidence.

## Boundary preserved

Story 115.3 closes only the exact route-local status+limit read boundary. It does not introduce or approve browser dashboard status+limit consumption, offset/cursor/page traversal, next-page token semantics, sorting controls, free-text search, arbitrary filters, saved searches, hidden discovery, status+limit+anything or broader selector composition, automatic row drill-down, task detail/digest/history/trace/replay/session traversal, replay execution target calls, lifecycle apply/prune/rollback, broad dashboard wiring, generated live data, browser-side LLM behavior, polling/timers/background refresh, workers, side channels, storage writes, automatic retry, mutation/control calls, services/MCP changes, dependencies, lockfiles, CI/deployment file changes, production credentials, production operations, or any unplanned adjacent surface.

Generated: 2026-06-28T20:06:00+03:00
