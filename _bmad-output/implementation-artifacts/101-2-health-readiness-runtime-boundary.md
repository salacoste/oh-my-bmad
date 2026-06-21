# Story 101.2 — Health/Readiness Runtime Boundary Implementation

## Status
Done — implemented the first narrow dashboard live-read runtime boundary for exactly `GET /v1/health`.

## Scope delivered
Story 101.2 converts the health panel from inert provenance-only copy into a single audited browser/runtime read boundary:

- Adds exactly one executable dashboard runtime file: `dashboard/static/health-readiness.js`.
- Mounts exactly one external script in `dashboard/static/index.html`: `health-readiness.js` with `defer`.
- Calls exactly one route from dashboard runtime code: `GET /v1/health`.
- Adds runtime-boundary contract tests in `tests/dashboard/test_health_readiness_runtime_boundary.py`.
- Updates existing read-only/static dashboard tests to allow only this one local runtime script while keeping all other runtime/network/control surfaces fail-closed.

## Guardrails preserved
Story 101.2 does **not** add broad live dashboard wiring. It does not add task detail, event timeline/transitions, trace, history, replay, lifecycle, aggregate, session, digest, stream, backend/API route, dependency, lockfile, deployment, CI, service, MCP, generated live-data, polling, streaming, background job, service worker, storage/cache, beacon, WebSocket, EventSource, form, button, input, operator control, mutation, or destructive lifecycle behavior.

## Runtime behavior
The health runtime module:

1. Starts in bounded non-authoritative loading/unavailable copy.
2. Performs one approved `fetch("/v1/health", { method: "GET" })` call.
3. Renders healthy `registry_status=ok` and `worker_status=ok` as authoritative health success.
4. Renders idle/stale, degraded/backend-unavailable, non-2xx, 401/403-like, invalid JSON, unexpected shape, and network failure cases as bounded non-authoritative copy.
5. Keeps visible source route, retrieved-at/freshness, authority, and detail metadata in the health panel.

## Test coverage
Added/updated tests prove:

- runtime script allowlist is exact;
- runtime module graph is closed to one file with no imports, dynamic imports, workers, service workers, preload/modulepreload assets, or secondary JS entrypoints;
- runtime route allowlist is exact: only `/v1/health`;
- runtime method is GET-only;
- success/degraded/backend-unavailable/invalid/unauthorized/network cases render correctly;
- old read-only/static/fixture contracts stay green with the precise one-script exception.

## Changed-file scope
Implementation-scope files:

- `dashboard/static/health-readiness.js`
- `dashboard/static/index.html`
- `tests/dashboard/test_health_readiness_runtime_boundary.py`
- `tests/dashboard/test_read_only_boundary.py`
- `tests/dashboard/test_static_shell.py`
- `_bmad-output/implementation-artifacts/101-2-health-readiness-runtime-boundary.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

## Verification
Required targeted verification:

- `uv run pytest -q tests/dashboard/test_health_readiness_runtime_boundary.py`
- `uv run pytest -q tests/dashboard/test_read_only_boundary.py tests/dashboard/test_static_shell.py tests/dashboard/test_static_fixture_rendering.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_live_read_adapter.py`
- static allowlist check proving exactly one runtime JS file, exactly one script tag, exactly one runtime `/v1/health` route, GET-only, and no forbidden broad runtime markers;
- `git diff --check`.

## AI slop cleanup report

Scope: Story 101.2 changed files listed above.

Behavior lock:
- Red Story 101.2 tests were added before implementation and failed for the missing script/module/metadata/runtime behavior.
- New Story 101.2 tests pass after implementation.
- Existing dashboard read-only/static/fixture tests pass after the one-module exception.

Cleanup plan:
1. Keep the implementation one-file/one-route only.
2. Prefer explicit source scanners and static DOM-target assertions over new dependencies.
3. Avoid helper modules/imports, polling, storage/cache, service workers, or speculative abstractions.
4. Preserve repeated guardrail language where it prevents future broad live-wiring drift.

Fallback/slop findings:
- No masking fallback, broad compatibility shim, swallowed error, silent default, speculative helper module, hidden HTTP client, extra dependency, or new backend route found.
- Network/parse/authorization failure branches are bounded fail-closed runtime states, not masking fallback slop, because they preserve visible non-authoritative evidence.

Remaining risk:
- Runtime behavior is validated with a lightweight Node/vm harness from pytest instead of a full browser e2e runner. This is acceptable for the first narrow boundary but a later broader live-read story should consider browser-level UltraQA coverage.
