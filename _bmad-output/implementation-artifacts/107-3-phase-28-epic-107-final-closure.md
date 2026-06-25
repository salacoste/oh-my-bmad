# Story 107.3 — Phase 28 / Epic 107 Final Validation Closure

## Status

Done — docs/status-only final validation and closure for Phase 28 / Epic 107 after Story 107.2 review, UltraQA, push, and remote CI evidence.

## Closed runtime surface

Epic 107 implemented the narrow Snapshot Creation authorization boundary selected by Story 107.1 and delivered by Story 107.2:

- `POST /v1/events/replay/snapshots` only.
- Existing authorization source only: Registry API JWT authentication via `JwtAuthMiddleware` / `JwtAuthSettings` (`Authorization: Bearer <JWT>` -> `request.state.authenticated is True`, `request.state.actor_id`).
- Visible operator initiation only through the dashboard bearer-token input and create button.
- Body-free POST; exact HTTP `201` required before authoritative metadata rendering.
- Bounded success metadata only: snapshot id, sequence number, timestamp, size, request/correlation id when available, authority/provenance/freshness.

This closure is surface-specific. It does not introduce or approve lifecycle apply/prune/rollback, destructive lifecycle authorization execution, archive/manifest mutation, snapshot deletion/restore, snapshot internals browsing, task-list/search/discovery, aggregate/session/digest, generated live data, replay execution target selection, hidden/background writes, broad dashboard wiring, additional controls, services, MCP changes, dependencies, lockfiles, CI workflow expansion, deployment changes, production credentials, or production operations.

## Story completion evidence

- Story 107.1 — Snapshot creation authorization planning: done in commit `467fc5d docs: open Phase 28 snapshot creation planning`.
- Story 107.2 — Snapshot creation authorization runtime boundary: done in commit `e8bb1e4 feat(dashboard): add snapshot creation authorization boundary`.
- Story 107.2 RALPLAN evidence:
  - Deep interview: `.omx/interviews/story-107-2-snapshot-creation-authorization-runtime-boundary-deep-interview.md`
  - RALPLAN: `.omx/specs/story-107-2-snapshot-creation-authorization-runtime-boundary-ralplan.md`
  - Test spec: `.omx/specs/story-107-2-snapshot-creation-authorization-runtime-boundary-test-spec.md`
  - Architect review: `.omx/specs/story-107-2-snapshot-creation-authorization-runtime-boundary-architect-review.md` — approve / CLEAR.
  - Critic review: `.omx/specs/story-107-2-snapshot-creation-authorization-runtime-boundary-critic-review.md` — final RALPLAN findings resolved before execution.
- Story 107.2 code review: `.omx/specs/story-107-2-snapshot-creation-authorization-runtime-boundary-code-review.md` — final verdict `APPROVE`; reviewer subagent `019f0018-9d05-7ec3-aff3-3c55110aa2dd`.
- Story 107.2 UltraQA: `.omx/ultraqa/story-107-2-snapshot-creation-authorization-runtime-boundary-report.md` — verdict `PASS`; verifier subagent `019f0018-9e5b-7ac3-8af3-6a14702764e8`.
- Local PR-gate CI-equivalent before push: `just test` -> `4324 passed, 8 skipped, 61 deselected, 41 warnings in 164.59s (0:02:44)`.
- Remote CI: GitHub Actions `ci` run `28195545005` succeeded for head `e8bb1e49b4ac03141dac2c7b63cd8b050a60d462`.
- CI URL: `https://github.com/salacoste/oh-my-bmad/actions/runs/28195545005`.
- Live CI recheck for this closure pass: `gh run view 28195545005 --repo salacoste/oh-my-bmad --json databaseId,headSha,conclusion,status,workflowName,url,jobs` returned workflow `ci`, status `completed`, conclusion `success`; jobs `Registry-state tests (Postgres service container)` and `PR gate (ruff + mypy + pytest)` both succeeded.

## Story 107.2 boundary evidence cited by closure

Story 107.2 proves the runtime remains intentionally narrow:

1. Backend snapshot creation authorization is route-local to exactly `POST /v1/events/replay/snapshots`.
2. `_require_snapshot_create_authorized(request)` runs before `_create_snapshot` or filesystem writes.
3. Missing, malformed, expired, invalid-signature, and JWT-disabled `X-Actor-Id` fallback states return `401` before snapshot creation.
4. The dashboard exposes a visible token input and create button; no page-load, timer, polling, storage/hash/query, worker, websocket/xhr side channel, cache-warming, automatic retry, or unrelated control can create snapshots.
5. The dashboard POST has no request body, uses the exact route, and requires exact HTTP `201` before authoritative metadata rendering.
6. Non-201 success, invalid JSON, malformed metadata, backend failure, network rejection, timeout/unknown outcome, and unauthorized states render non-authoritative copy and do not auto-repeat `POST`.
7. Duplicate in-flight clicks create one request only; any later creation requires a fresh visible operator action.
8. Tokens are not echoed or stored, and GET snapshot-list behavior remains unauthenticated/read-only as previously scoped.
9. Lifecycle apply/prune/rollback, archive/manifest mutation, snapshot deletion/restore, task-list/search/discovery, aggregate/session/digest, generated live data, replay execution target selection, broad dashboard wiring, services/MCP/dependencies/deployment changes, production credentials, and production operations remain unreachable.

## Changed files across Epic 107 runtime and closure

Planning/status/docs:

- `_bmad-output/planning-artifacts/phase-28-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-28-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-28-epics.md`
- `_bmad-output/implementation-artifacts/107-1-snapshot-creation-authorization-planning.md`
- `_bmad-output/implementation-artifacts/107-2-snapshot-creation-authorization-runtime-boundary.md`
- `_bmad-output/implementation-artifacts/107-3-phase-28-epic-107-final-closure.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `README.md`
- `docs/index.md`
- `docs/architecture.md`
- `docs/feature-status.md`

Runtime and tests from Story 107.2:

- `dashboard/static/index.html`
- `dashboard/static/lifecycle-snapshot.js`
- `services/registry-api/src/registry_api/routes/replay.py`
- `services/registry-api/src/registry_api/routes/test_replay.py`
- `tests/dashboard/test_lifecycle_snapshot_runtime_boundary.py`
- `tests/dashboard/test_live_read_contracts.py`
- `tests/dashboard/test_read_only_boundary.py`
- `tests/dashboard/test_static_shell.py`
- `tests/dashboard/test_event_timeline_runtime_boundary.py`
- `tests/dashboard/test_health_readiness_runtime_boundary.py`
- `tests/dashboard/test_history_replay_runtime_boundary.py`
- `tests/dashboard/test_phase20_final_validation.py`
- `tests/dashboard/test_static_fixture_rendering.py`
- `tests/dashboard/test_task_detail_runtime_boundary.py`
- `tests/dashboard/test_trace_correlation_runtime_boundary.py`

## Sprint-status closure

Sprint status now marks:

- `current_phase: 28` with Phase 28 closed.
- `epic-107: done`
- `107-1-snapshot-creation-authorization-planning: done`
- `107-2-snapshot-creation-authorization-runtime-boundary: done`
- `107-3-phase-28-epic-107-final-closure: done`

Phase 28 is closed for the Snapshot Creation authorization boundary only. Future route families, destructive lifecycle operations, archive/manifest mutation, snapshot deletion/restore, broader dashboard wiring, and additional controls require separate product/architecture selection, implementation tests, independent review, QA, push, and CI evidence.

## Final docs/status verification plan

Story 107.3 final verification is docs/status-only:

- Verify `sprint-status.yaml` parses as YAML and marks Epic 107 stories done.
- Verify exact Phase 28 / Epic 107 status strings and remote CI evidence references are present in status docs.
- Verify the explicit non-authorization list includes lifecycle apply/prune/rollback, archive/manifest mutation, snapshot deletion/restore, task-list/search/discovery, aggregate/session/digest, generated live data, replay execution target selection, broad dashboard live wiring, services/MCP/dependencies/CI expansion, additional controls, and production operations.
- Run `git diff --check`.

## UltraQA decision for Story 107.3

This closure pass is docs/status-only. If final diff remains limited to this artifact, sprint/status documentation, and OMX workflow evidence, adversarial runtime UltraQA is skipped for Story 107.3 because runtime behavior is locked by Story 107.2's completed code review, UltraQA PASS report, local PR-gate evidence, and green remote CI run `28195545005`. Any runtime/source/test/backend/API/dependency/CI/service/MCP/generated-data diff after Story 107.2 would invalidate this skip and require returning to implementation/review/QA scope.

Generated: 2026-06-25T19:41:22Z
