# Story 94.2 — Provenance, freshness, stale/error-state contract tests

Status: done

## Scope

Implement Phase 20 Story 94.2 as a contract-test-only safety slice before any live dashboard wiring.

This story defines required source/provenance, timestamp/freshness, identifier, and degraded-state semantics for future live dashboard values. It intentionally does not implement live data fetching, adapters, backend routes, dashboard JavaScript wiring, digest integration, aggregate/session-list contract approval, or mutation/control behavior.

## Guardrails

- No live dashboard wiring.
- No frontend JavaScript, polling, automatic refresh, `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource`, HTMX, data-endpoint, or client-side route calls in shipped dashboard assets.
- No backend/API route implementation or adapter implementation.
- No digest integration in this story.
- No aggregate/session-list read contract approval in this story.
- No destructive lifecycle apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled jobs, credentialed production operation, or mutation/control surface.
- No CI/dependency/deployment changes.

## Implementation evidence

- Added `tests/dashboard/test_live_read_state_contracts.py` as the dedicated Story 94.2 provenance/freshness/degraded-state contract module.
- The module imports Story 94.1 `tests.dashboard.test_live_read_contracts` and reuses its approved-route and needs-separate-contract route inventory rather than duplicating route lists.
- Tests enforce:
  - every future live value family declares source category, route contract status, timestamp policy, freshness policy, applicable identifiers, and allowed display states;
  - aggregate/session families remain unavailable/needs-contract and are not approved live reads;
  - non-authoritative states (`unavailable`, `needs-contract`, `partial`, `stale`, `invalid`, `unauthorized`, `backend-unavailable`) render bounded uncertainty and cannot use healthy/authoritative/success copy;
  - invalid, partial, stale, or backend-unavailable replay/lifecycle states cannot render healthy;
  - synthetic guard-sensitivity probes fail when provenance, freshness, identifiers, route approval, or missing-contract degraded rendering are removed or weakened.

## Forward compatibility note

The state names, timestamp policies, freshness policies, and route/category vocabulary in this story are provisional contract scaffolding for Phase 20 safety gates. Story 95/96 live adapter or panel wiring must centralize the runtime schema/mapper before introducing live behavior, or update these contracts under review if the final runtime vocabulary intentionally changes.

## Initial validation evidence

```text
uv run pytest -q tests/dashboard/test_live_read_state_contracts.py
6 passed, 2 warnings
```

Final validation, review, QA, CI, and completion evidence will be appended before moving this story to done.


## Final validation evidence

- `uv run pytest -q tests/dashboard/test_static_shell.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_live_read_state_contracts.py` — 75 passed, 2 warnings.
- `git diff --check` — passed.
- `uv run ruff format --check .` — 577 files already formatted.
- `uv run ruff check .` — passed.
- `uv run mypy --strict --explicit-package-bases packages/ services/registry-api services/registry-state` — success, 182 source files.
- `uv run pytest -q -m "not slow"` — 4210 passed, 8 skipped, 61 deselected, 25 warnings.

## QA disposition

UltraQA is expected to be skipped for this story after independent review if the final diff remains tests/docs/status only and introduces no runtime/user-facing dashboard behavior. The contract and regression gates above are the applicable adversarial proof for this slice.
