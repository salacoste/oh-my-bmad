# Story 95.1 — Minimal read adapter boundary for existing per-task safe reads

Status: done

## Scope
Implement the Phase 20 adapter boundary for approved existing per-task safe reads. This story introduces a pure Python boundary that describes route/source/freshness/error metadata and request descriptors. It does not wire static dashboard panels to live data and does not add backend/API routes.

## Implemented artifacts
- `dashboard/live_read_adapter.py`
  - Frozen metadata/request/result dataclasses.
  - Exact approved GET route inventory for the nine Story 94.1 safe reads.
  - Source category, timestamp policy, freshness policy, required identifiers, and allowed display states aligned with Story 94.2.
  - Explicit digest-route exclusion.
  - Aggregate overview and session list remain `needs-separate-contract` unavailable descriptors.
- `tests/dashboard/test_live_read_adapter.py`
  - Exact route inventory comparison against Story 94.1 contracts.
  - GET-only allowlist proof for request descriptors.
  - Metadata comparison against Story 94.2 state contracts.
  - Digest, aggregate, and session exclusion checks.
  - Fail-closed metadata checks for non-authoritative states.
  - Existing executable-surface forbidden-marker guard over the adapter module.
  - Static dashboard inertness regression.

## Non-goals preserved
- No static dashboard live wiring.
- No frontend `fetch` or polling.
- No backend/API route implementation.
- No digest integration.
- No aggregate/session read approval.
- No mutation/control surface.
- No dependency, CI, or deployment change.

## Verification evidence
- Initial targeted dashboard regression: `uv run pytest -q tests/dashboard/test_live_read_contracts.py tests/dashboard/test_live_read_state_contracts.py tests/dashboard/test_live_read_adapter.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_static_shell.py` → 82 passed, 2 warnings.

## Review status
Autopilot Ralplan consensus before implementation:
- Native Architect: APPROVE/CLEAR (`019ed7bf-2e00-79c2-864c-0671e3f45998`).
- Native Critic: APPROVE (`019ed7c2-75f4-7dd1-b792-c6b9137dc763`).

Final verification, cleanup, and independent code review are complete. Commit/push/CI and Ultragoal checkpoint are pending at this artifact update.

## Final review evidence
- Independent code-reviewer `019ed9cf-7473-7200-841e-8facb4f7f08e`: APPROVE, no findings.
- Independent architect `019ed7d3-9306-7263-ad7a-57b2b64b36cf`: CLEAR after route-centric API and immutable identifier fixes.

## Final local gate evidence
- `uv run ruff format --check .` passed.
- `git diff --check` passed.
- `uv run ruff check .` passed.
- `uv run mypy --strict --explicit-package-bases packages/ services/registry-api services/registry-state dashboard/live_read_adapter.py tests/dashboard/test_live_read_adapter.py` passed.
- Targeted dashboard suite passed: 82 passed.
- Full non-slow suite passed after route-centric fix: 4217 passed, 8 skipped, 61 deselected.
