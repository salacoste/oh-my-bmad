# Story 94.1 — Route/method/effect allowlist contract tests

Status: done

## Scope

Implement Phase 20 Story 94.1 as a contract-test-only safety slice before any live dashboard wiring.

This story adds a dedicated dashboard live-read contract test layer proving future dashboard live-read code stays inside a candidate/provisional GET-only allowlist and cannot trigger mutation/control behavior by method, route, import, effect, or vocabulary.

## Guardrails

- No live dashboard wiring.
- No frontend JavaScript, polling, automatic refresh, `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource`, HTMX, data-endpoint, or client-side route calls in shipped dashboard assets.
- No backend/API route implementation or adapter implementation.
- No digest integration in this story.
- No aggregate/session-list read contract approval in this story.
- No destructive lifecycle apply, prune, delete, truncate, move, rewrite, chmod, archive mutation, manifest mutation, scheduled jobs, credentialed production operation, or mutation/control surface.
- No CI/dependency/deployment changes.

## Implementation evidence

- Added `tests/dashboard/test_live_read_contracts.py` as the dedicated Story 94.1 contract-test module.
- The contract module aliases/imports `CORE_APPROVED_READ_ROUTES`, `OPTIONAL_NON_CORE_READ_ROUTES`, and `FORBIDDEN_METHODS` from `tests/dashboard/test_read_only_boundary.py`; it does not copy the candidate/provisional core read-route inventory.
- Tests enforce:
  - candidate/provisional route uniqueness, normalization, and GET-only methods;
  - `POST`, `PUT`, `PATCH`, and `DELETE` rejection for dashboard route candidates;
  - digest route remains non-core/excluded from Story 94.1;
  - aggregate/session/list/control-shaped GET routes need separate contracts;
  - static dashboard assets keep `/v1/` references out of executable/actionable contexts;
  - dashboard executable surfaces do not contain writer, lifecycle, snapshot, job, idempotency, cache, storage, beacon, service-worker, timer, websocket/eventsource, or actionable mutation vocabulary markers;
  - synthetic guard-sensitivity probes fail on forbidden methods, unapproved GET routes, digest live calls, writer/lifecycle/snapshot/cache/job/idempotency markers, and hidden background mechanisms.

## Validation evidence

Initial local targeted check:

```text
uv run pytest tests/dashboard/test_live_read_contracts.py -q
9 passed, 2 warnings
```

Final validation, review, QA, CI, and completion evidence will be appended before moving this story to done.

## Final validation evidence

- `uv run pytest tests/dashboard/test_live_read_contracts.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_static_shell.py -q` — 69 passed, 2 warnings.
- `uv run pytest -q -m "not slow"` — 4204 passed, 8 skipped, 61 deselected, 29 warnings after the idempotency sensitivity fix.
- `git diff --check` — passed.
- `uv run ruff format --check .` — 576 files already formatted.
- `uv run ruff check .` — passed.
- `uv run mypy --strict --explicit-package-bases packages/ services/registry-api services/registry-state` — success, 182 source files.

## Cleanup/review evidence

- ai-slop-cleaner scoped pass: no cleanup edits required; fallback-like scan hits were historical sprint-status text outside Story 94.1 changes.
- Independent code-reviewer: `APPROVE`, no findings after adding the explicit `idempotency cache mutation write` sensitivity probe.
- Independent architect: `CLEAR`, no findings.

## QA disposition

UltraQA is skipped for this story because the final diff is tests/docs/status only and introduces no runtime/user-facing dashboard behavior. The regression and contract-test gates above are the applicable adversarial proof for this slice.
