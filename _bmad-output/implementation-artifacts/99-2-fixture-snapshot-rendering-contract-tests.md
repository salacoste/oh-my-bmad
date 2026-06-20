# Story 99.2 — Fixture/snapshot rendering contract tests

Status: done
Date started: 2026-06-20

## Scope

Implement Phase 21 Story 99.2 as a test-first fixture/snapshot contract slice before static dashboard rendering or live dashboard wiring.

## Pre-implementation review evidence

Fresh sequential reviews were rerun before Ultragoal implementation:

- Architect: `CLEAR_WITH_NOTES` — no blockers; required separate fixture dataclasses, explicit state generation/probe path, and a dedicated fixture contract test file.
- Critic: `CLEAR_WITH_NOTES` — no blockers; required a dedicated test file, distinct Story 99.2 fixture schema, deterministic public validation/build path, no production forbidden-marker collisions, and concrete source identifiers.

## Implemented contract

- Added distinct frozen Story 99.2 fixture dataclasses in `dashboard/live_read_adapter.py`:
  - `SourceIdentifier`
  - `RouteFixtureRow`
  - `PanelFixtureSnapshot`
  - `RouteFixtureProbe`
- Added Story 99.2 fixture builders and probes derived from approved Story 99.1/Phase 20 panel routes only.
- Fixture rows carry panel family, inert `source_route_pattern`, source category, route-input identifier labels, row-display identifier labels, source identifiers, timestamp/freshness policies, fixture provenance, fixture freshness/timestamp labels, display state, degraded-state category, authority state, display severity, bounded display copy, renderer context fields, and a non-rendered `read_only_contract` marker.
- Healthy fixture copy is scoped to static fixture/readiness and contract-fixture authority only; runtime data remains disconnected.
- Degraded fixture states remain explicit, non-normal, and non-authoritative or needs-contract.
- Aggregate/session/digest routes remain fail-closed and absent from renderable fixture snapshots:
  - `/v1/tasks`
  - `/v1/sessions`
  - `/v1/sessions/{session_id}`
  - `/v1/tasks/{task_id}/logs/digest`
  - `/v1/tasks/{task_id}/logs/digest/stream`
- Public validation rejects unsafe synthetic fixture rows deterministically instead of silently filtering them.

## Safety boundaries

- No dashboard static HTML changes.
- No browser scripts, fetch/XHR/WebSocket/EventSource/polling, or runtime live wiring.
- No backend/API route expansion.
- No aggregate/session live contract.
- No digest integration.
- No mutation/control/destructive lifecycle affordance.
- No dependency, lockfile, CI/deployment, service, MCP server, or generated live-data changes.

## Verification evidence

Red/green fixture contract gate:

- Initial `uv run pytest -q tests/dashboard/test_live_read_fixture_contracts.py` failed with 7 missing Story 99.2 fixture APIs before implementation.
- After implementation and one schema-assertion correction, `uv run pytest -q tests/dashboard/test_live_read_fixture_contracts.py` → 7 passed, 2 warnings.

Focused regression gates:

- `uv run pytest -q tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_static_shell.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_read_only_boundary.py` → 88 passed, 2 warnings.
- `uv run ruff check dashboard/live_read_adapter.py tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_live_read_state_contracts.py` → passed.
- `uv run ruff format --check dashboard/live_read_adapter.py tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_live_read_state_contracts.py` → passed after formatting the two changed Python files.
- `uv run mypy --strict --explicit-package-bases dashboard/live_read_adapter.py tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_live_read_state_contracts.py` → passed.

## Review-cycle fix evidence

The first final architect lane returned `WATCH` for one semantic concern: `source_identifiers` merged route-input identifiers with row-display identifiers, which blurred source-route identity and display-context identity.

Applied fix:

- `source_identifiers` now contains exact route-source identifiers only.
- Routes without input identifiers use an inert source-category fixture identifier.
- Row-display identifiers remain a separate field.
- Public validation rejects synthetic rows that add display-only identifiers into `source_identifiers`.

Post-fix local gates:

- `uv run pytest -q tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_static_shell.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_read_only_boundary.py` → 88 passed, 2 warnings.
- `uv run ruff check dashboard/live_read_adapter.py tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_live_read_state_contracts.py` → passed.
- `uv run ruff format --check dashboard/live_read_adapter.py tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_live_read_state_contracts.py` → passed.
- `uv run mypy --strict --explicit-package-bases dashboard/live_read_adapter.py tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_live_read_state_contracts.py` → passed.
- `git diff --check` → passed.
- YAML parse and changed-file allowlist/guardrails → passed.
- `uv run pytest -q -m "not slow"` → 4248 passed, 8 skipped, 61 deselected, 27 warnings.

## Code-review fix evidence

The first post-WATCH code-reviewer re-review returned `REQUEST CHANGES` for Pyright/LSP diagnostics in runtime-loaded test module type annotations.

Applied fix:

- Added `TYPE_CHECKING` imports and direct type-only aliases in Story 99.2 fixture tests.
- Applied the same annotation-only fix to Story 99.1 state tests because the reviewer's full Pyright command included that required verification file and exposed matching pre-existing diagnostics.

Post-fix local gates:

- `uvx pyright dashboard/live_read_adapter.py tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_live_read_state_contracts.py` → 0 errors, 0 warnings.
- `uv run pytest -q tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_static_shell.py` → 70 passed, 2 warnings.
- `uv run pytest -q tests/dashboard/test_live_read_contracts.py tests/dashboard/test_read_only_boundary.py` → 18 passed, 2 warnings.
- `uv run ruff check dashboard/live_read_adapter.py tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_live_read_state_contracts.py` → passed.
- `uv run ruff format --check dashboard/live_read_adapter.py tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_live_read_state_contracts.py` → passed.
- `uv run mypy --strict --explicit-package-bases dashboard/live_read_adapter.py tests/dashboard/test_live_read_fixture_contracts.py tests/dashboard/test_live_read_state_contracts.py` → passed.
- `git diff --check` → passed.
- YAML parse and changed-file allowlist/guardrails → passed.

Final independent review evidence:

- Code-reviewer final re-review: `APPROVE`, 0 issues. Prior Pyright/LSP blocker resolved with `TYPE_CHECKING` imports and direct type-only annotations.
- Architect final re-review: `CLEAR`. No remaining architecture concern after the source/display identity split and Pyright-only test annotation fix.

UltraQA is skipped clean for Story 99.2 because the diff is pure Python contract/test/status metadata and does not alter static HTML, browser scripts, backend/API routes, runtime wiring, dependencies, services, MCP servers, or user-facing runtime behavior. Contract tests, boundary tests, static shell tests, Pyright, strict mypy, lint/format, allowlist checks, and broad non-slow regression are the appropriate adversarial evidence for this slice.

## Completion evidence

- Story 99.2 fixture/snapshot contract tests exist and pass.
- Fixture schema remains static/readiness-only and cannot masquerade as live backend state.
- Aggregate/session/digest routes remain unavailable/fail-closed.
- BMad story/status updated to done.
- Code-reviewer final re-review returned APPROVE; architect final re-review returned CLEAR.
- UltraQA skipped clean for pure contract/test/status scope.
- Local broad regression passed: `uv run pytest -q -m "not slow"` → 4248 passed, 8 skipped, 61 deselected.
- Push/CI evidence remains pending until this local change set is committed and pushed.
