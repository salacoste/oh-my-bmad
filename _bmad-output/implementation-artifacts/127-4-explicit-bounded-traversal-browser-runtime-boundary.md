# Story 127.4 — Explicit Bounded Traversal / Infinite-Scroll Mode

## Scope

Story 127.4 implements explicit bounded traversal for the dashboard aggregate task-list search path only. It does not change backend/API behavior, dependencies, lockfiles, broad dashboard wiring, mutation/control routes, credentials, deployment, or production operations.

## Implemented contract

- Added visible traversal controls:
  - `aggregate-task-list-traversal-budget-control` (`1..5` pages)
  - `aggregate-task-list-traversal-rate-control` (`one_page_per_response`)
  - `aggregate-task-list-traversal-enable`
  - `aggregate-task-list-traversal-cancel`
  - `aggregate-task-list-traversal-state`
- Traversal is available only after a healthy authoritative search response with `has_more=true` and numeric `next_offset`.
- Enable validates visible budget/rate controls and the exact unchanged visible search selector tuple.
- Each page updates the visible offset control, revalidates the visible tuple, and reads the same canonical raw search route with `credentials: "omit"` and no request body.
- Traversal stops on budget exhaustion, no next offset, visible stop control, selector edit/mismatch, invalid/stale/non-authoritative/malformed response, unauthorized/backend/network failure, or hidden/invalid traversal controls, while preserving terminal budget/pages_read accounting on fail-closed active traversal exits.
- Disabled mode remains inert: `has_more`/`next_offset` alone causes no automatic next read, prefetch, timer, worker, observer, web-socket/event-source/XMLHttpRequest side channel, repeated-attempt loop, or cache warming.

## Changed files

- `dashboard/static/aggregate-task-list.js`
- `dashboard/static/index.html`
- `tests/dashboard/test_aggregate_task_list_runtime_boundary.py`
- dashboard static/read-only allowlist tests for the new approved visible traversal controls
- `docs/feature-status.md`
- `_bmad-output/planning-artifacts/phase-48-production-readiness-epics.md`
- `.omx/plans/story-127-4-explicit-bounded-traversal-plan.md`

## Verification

- `uv run pytest tests/dashboard/test_aggregate_task_list_runtime_boundary.py -q` → 28 passed.
- `uv run pytest tests/dashboard -q` → 238 passed.
- `uv run ruff format --check dashboard tests` → pass.
- `uv run ruff check dashboard tests` → pass.
- `uv run mypy --strict --explicit-package-bases dashboard tests/dashboard` → pass.
- `git diff --check` → pass.
- Final code-review gate → APPROVE.
