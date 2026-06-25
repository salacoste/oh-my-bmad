# Story 107.2 — Snapshot Creation Authorization Runtime Boundary

## Status

Done — local code review, UltraQA, strict typecheck, lint/format, and PR-gate CI-equivalent passed.

## Selected surface

- `POST /v1/events/replay/snapshots` only.

## Pinned existing authorization source

Story 107.2 reuses exactly one existing source: Registry API JWT authentication via `JwtAuthMiddleware` / `JwtAuthSettings` (`Authorization: Bearer <JWT>` -> `request.state.authenticated is True`, `request.state.actor_id`). No new credential system, backend auth middleware, service token, capability tier, production credential dependency, dashboard credential storage, credential echoing, or credential persistence is authorized.

## Scope delivered

- Backend route-local authorization boundary for snapshot creation only:
  - `services/registry-api/src/registry_api/routes/replay.py`
  - `create_snapshot_endpoint()` now calls `_require_snapshot_create_authorized(request)` before `_create_snapshot` or filesystem writes.
  - Fail-closed unauthorized states return HTTP 401 before snapshot creation.
- Dashboard visible operator affordance:
  - `dashboard/static/index.html`
  - `dashboard/static/lifecycle-snapshot.js`
  - Adds a visible bearer-token input and create button for the exact `POST /v1/events/replay/snapshots` route.
  - POST is click-only, body-free, no query/hash, no hidden timers/background writes, no automatic repeat request, duplicate/in-flight clicks are blocked, and only exact HTTP 201 renders metadata.
- Boundary/test updates preserve existing GET snapshot-list behavior and older dashboard read-only guards by allowing only the Story 107.2 approved controls/POST exception.

## Non-authorization / exclusions

This story does not authorize lifecycle apply/prune/rollback, destructive lifecycle authorization execution, archive/manifest mutation, snapshot deletion/restore, snapshot internals browsing, task-list/search/discovery, aggregate/session/digest, generated live data, replay execution target selection, broad dashboard wiring, services/MCP/dependencies/deployment changes, production credentials, credential persistence, or production operations.

## Red-test evidence

- Backend fail-closed red baseline initially failed as expected before implementation:
  - Command: `uv run pytest services/registry-api/src/registry_api/routes/test_replay.py::TestCreateSnapshotAuthorizationRuntimeBoundary::test_jwt_disabled_x_actor_id_fallback_fails_closed_before_snapshot_create -q`
  - Evidence: current behavior returned `201 Created` and invoked `_create_snapshot` for JWT-disabled `X-Actor-Id` fallback; test expected `401` and zero writes.
- Dashboard red baselines failed as expected before implementation/fix:
  - Missing visible Story 107.2 create affordance.
  - Missing exact click-only POST behavior and no-repeat/duplicate/fail-closed runtime handling.
  - Review-fix regression: `200 OK` plus valid-shaped metadata rendered as created before enforcing exact HTTP 201.

## Green verification / CI evidence

- Targeted Story 107.2 + adjacent boundary suite:
  - `uv run pytest tests/dashboard/test_lifecycle_snapshot_runtime_boundary.py tests/dashboard/test_read_only_boundary.py services/registry-api/src/registry_api/routes/test_replay.py -q`
  - Result: `62 passed, 2 warnings in 2.88s`.
- Full dashboard regression suite:
  - `uv run pytest tests/dashboard -q`
  - Result: `180 passed, 2 warnings in 6.06s`.
- Affected backend replay route suite:
  - `uv run pytest services/registry-api/src/registry_api/routes/test_replay.py -q`
  - Result: `35 passed, 1 warning in 0.82s`.
- Static/lint/syntax/type checks:
  - `uv run ruff format --check services/registry-api/src/registry_api/routes/test_replay.py tests/dashboard/test_lifecycle_snapshot_runtime_boundary.py tests/dashboard/test_live_read_contracts.py tests/dashboard/test_read_only_boundary.py tests/dashboard/test_static_shell.py`
  - Result: `5 files already formatted`.
  - `uv run ruff check services/registry-api/src/registry_api/routes/replay.py services/registry-api/src/registry_api/routes/test_replay.py tests/dashboard`
  - Result: `All checks passed!`.
  - `uv run mypy --strict --explicit-package-bases packages/ services/registry-api services/registry-state`
  - Result: `Success: no issues found in 182 source files`
  - `node --check dashboard/static/lifecycle-snapshot.js`
  - Result: passed.
  - `git diff --check`
  - Result: passed.
- Local PR-gate CI-equivalent:
  - `just test` (`uv run pytest -m "not slow"`)
  - Result: `4324 passed, 8 skipped, 61 deselected, 41 warnings in 164.59s (0:02:44)`.

## Evidence checklist

- Deep interview: `.omx/interviews/story-107-2-snapshot-creation-authorization-runtime-boundary-deep-interview.md`
- RALPLAN: `.omx/specs/story-107-2-snapshot-creation-authorization-runtime-boundary-ralplan.md`
- Test spec: `.omx/specs/story-107-2-snapshot-creation-authorization-runtime-boundary-test-spec.md`
- Architect review: `.omx/specs/story-107-2-snapshot-creation-authorization-runtime-boundary-architect-review.md` (approve/CLEAR)
- Critic review: `.omx/specs/story-107-2-snapshot-creation-authorization-runtime-boundary-critic-review.md` (approve)
- Red tests: complete
- Implementation: complete
- Verification: complete locally
- Code review: `.omx/specs/story-107-2-snapshot-creation-authorization-runtime-boundary-code-review.md` (APPROVE)
- UltraQA: `.omx/ultraqa/story-107-2-snapshot-creation-authorization-runtime-boundary-report.md` (PASS)

Generated: 2026-06-25T18:13:09Z
Updated: 2026-06-25T18:54:04Z
