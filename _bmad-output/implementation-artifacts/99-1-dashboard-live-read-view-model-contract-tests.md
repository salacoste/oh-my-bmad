# Story 99.1 — Dashboard live-read view-model contract tests

Status: done
Date started: 2026-06-19

## Scope

Implement Phase 21 Story 99.1 as a test-first presentation/view-model contract slice before any live dashboard wiring.

## Implemented contract

- Added pure inert Story 99.1 view-model contracts in `dashboard/live_read_adapter.py`.
- View models are generated only from approved Phase 20 Story 96.1 and 96.2 panel metadata.
- Every renderable route carries panel family, route pattern, source category, route input identifiers, row display identifiers, timestamp/freshness policy, display state, degraded-state category, authority state, display severity, contract/static/readiness copy, and a non-rendered `read_only_contract` boolean.
- Aggregate/session/digest routes remain fail-closed: `/v1/tasks`, `/v1/sessions`, `/v1/sessions/{session_id}`, `/v1/tasks/{task_id}/logs/digest`, and `/v1/tasks/{task_id}/logs/digest/stream`.

## Safety boundaries

- No dashboard static HTML changes.
- No browser scripts, fetch/XHR/WebSocket/EventSource/polling, or runtime live wiring.
- No backend/API route expansion.
- No aggregate/session live contract.
- No digest integration.
- No mutation/control/destructive lifecycle affordance.
- No dependency, lockfile, CI/deployment, service, or MCP server changes.

## Verification evidence

Initial focused red/green gate:

- `uv run pytest -q tests/dashboard/test_live_read_state_contracts.py` → 11 passed, 2 warnings.

Final review, UltraQA/skip, push, CI, and Ultragoal checkpoint evidence are pending before completion. Local implementation verification is green.

Additional local gates before review:

- `uv run pytest -q tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_read_only_boundary.py` → 29 passed, 2 warnings.
- `uv run pytest -q tests/dashboard/test_static_shell.py tests/dashboard/test_live_read_state_contracts.py` → 62 passed, 2 warnings.
- `uv run ruff check dashboard/live_read_adapter.py tests/dashboard/test_live_read_state_contracts.py` → passed.
- `uv run ruff format --check dashboard/live_read_adapter.py tests/dashboard/test_live_read_state_contracts.py` → passed.
- `uv run mypy --strict --explicit-package-bases dashboard/live_read_adapter.py tests/dashboard/test_live_read_state_contracts.py` → passed.
- `git diff --check` → passed.
- sprint-status YAML parse and changed-file allowlist/guardrail scan → passed.

## Review-cycle fix evidence

The first independent architect review returned WATCH, not BLOCK, for two future-drift risks:

1. duplicate panel route collisions could be silently overwritten by the Story 99.1 lookup;
2. forbidden aggregate/session/digest inventory was explicit but split across multiple lists.

Applied fixes:

- Story 99.1 forbidden renderable routes are now derived from unavailable read contracts, the explicit session-detail route, and excluded digest routes.
- Story 99.1 panel route lookup now fails fast on duplicate route patterns before rendering.
- Tests now verify the derived forbidden inventory and duplicate-route fail-fast behavior.

Post-fix local gates:

- `uv run pytest -q tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_read_only_boundary.py` → 30 passed, 2 warnings.
- `uv run pytest -q tests/dashboard/test_static_shell.py tests/dashboard/test_live_read_state_contracts.py` → 63 passed, 2 warnings.
- `uv run ruff check dashboard/live_read_adapter.py tests/dashboard/test_live_read_state_contracts.py` → passed.
- `uv run ruff format --check dashboard/live_read_adapter.py tests/dashboard/test_live_read_state_contracts.py` → passed.
- `uv run mypy --strict --explicit-package-bases dashboard/live_read_adapter.py tests/dashboard/test_live_read_state_contracts.py` → passed.
- `git diff --check` → passed.
- `uv run pytest -q -m "not slow"` → 4240 passed, 8 skipped, 61 deselected, 30 warnings.

## Independent review evidence

- Code-reviewer lane: APPROVE, 0 issues across code/spec/security/maintainability.
- Architect lane: initial WATCH for duplicate-route collision and forbidden-inventory drift risk; fixes applied and re-review returned CLEAR.
- UltraQA: skipped for this story because the diff is pure Python contract/test/status metadata and does not alter static HTML, browser scripts, backend/API routes, runtime wiring, dependencies, services, MCP servers, or user-facing runtime behavior. Contract and boundary tests plus broad non-slow regression are the appropriate adversarial evidence for this slice.

## Completion evidence

- Implementation commit: `efac7d5725ddd99a4156407bc46a919086bbc2ba`.
- GitHub Actions CI run: `27845888487` — `ci` workflow completed successfully.
  - Registry-state tests (Postgres service container): success.
  - PR gate (ruff + mypy + pytest): success.
- Story 99.1 remains a pure contract/test/status slice with no static HTML, browser scripts, backend/API route expansion, runtime live wiring, dependencies, services, MCP servers, aggregate/session live contract, digest integration, or mutation/control surface.
