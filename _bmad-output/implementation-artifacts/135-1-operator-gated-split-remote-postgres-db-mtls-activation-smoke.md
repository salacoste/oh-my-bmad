# Story 135.1: Operator-Gated Split Deployment / Remote Postgres / DB mTLS Activation Smoke

Status: done

## Story

As an operator,
I want a single operator-gated activation-smoke story for split deployment, remote Postgres, and DB mTLS,
so that real activation evidence is collected only under an approved change window with rollback and emergency-disable ownership.

## Activation Boundary

This story is an implementation-ready work item, not evidence that activation occurred. It may proceed to live smoke activity only after explicit operator approval, security approval, target environment/version, change window, rollback owner/plan, emergency-disable owner/plan, independent reviewer, redaction plan, and credential/certificate reference boundaries are supplied outside the repository.

Those prerequisites are absent for this local pass, so Story 135.1 records a repo-local blocked/no-go/fail-closed outcome only. No live split deployment activation, remote Postgres activation, DB mTLS production activation, live database cutover, migration execution, provisioning, production host mutation, credential/certificate handling, activation runtime/deployment config change, dependency/lock change, plaintext fallback, or production-state change was authorized or performed.

## Acceptance Criteria

1. **Operator gate before live smoke:** Explicit operator approval, security approval, target environment and version, change window, rollback owner and plan, emergency-disable owner and plan, independent reviewer, evidence retention/freshness, and redaction statement are present before any activation smoke can be attempted.
2. **Prerequisite evidence is not activation proof:** Phase 51 controlled activation evidence, Epic 132 split-deployment/remote Postgres readiness, and Epic 133 DB mTLS readiness are treated as prerequisites only. The story must not infer activation from planning or readiness artifacts.
3. **Fail-closed missing-gate behavior:** Missing, stale, ambiguous, or unredacted operator evidence leaves the story blocked/no-go or fail-closed without live smoke activity.
4. **Split deployment smoke evidence:** Evidence covers service placement, network boundary, registry-state single-writer authority, event-log append authority, MCP boundary, operator/dashboard ingress, health/readiness, rollback trigger, and post-smoke go/no-go decision.
5. **Remote Postgres smoke/migration evidence:** Evidence covers backup checkpoint, single migration runner authority, bounded pool settings, migration/rollback/fix-forward decision points, read-side compatibility, writer authority, redacted endpoint identity, and no plaintext fallback.
6. **DB mTLS smoke/failure evidence:** Evidence covers explicit `REGISTRY_DB_MTLS_ENABLED` gate state, server-side TLS and client certificate enforcement, approved secret references by identifier only, certificate expiry/revocation/hostname metadata, bounded failure diagnostics, and no plaintext fallback.
7. **Secret hygiene:** Saved artifacts contain no credential values, token values, private key material, certificate bodies, unredacted DSNs, production host secrets, or full secret paths.
8. **Scope control:** Any code/script/deployment/runtime change is explicitly justified inside this story and preserves local SQLite/default behavior. Unscoped dependencies, lockfiles, broad runtime behavior, CI, or production-state changes are forbidden.
9. **Verification evidence:** The implementation records the exact local/static verification commands used, and any operator-supplied live-smoke evidence must be sanitized, timestamped, target-scoped, independently reviewed, and tied to rollback/no-go criteria.
10. **Status semantics:** Done/review status for this story may only mean the scoped story outcome is recorded. It must not claim activation if the operator gate was unavailable or no live smoke activity occurred.

## Tasks / Subtasks

- [x] Validate operator gate inputs before any live-smoke step. (AC: 1, 3, 7)
  - [x] Record missing operator approval reference, security approval reference, target environment/version, and change window as blocked/no-go.
  - [x] Record missing rollback owner/plan, emergency-disable owner/plan, independent reviewer, and evidence retention/freshness rules as blocked/no-go.
  - [x] Confirm redaction plan and approved secret/certificate reference boundaries are absent; no secret values are written to repo artifacts.
- [x] Reconcile prerequisite readiness/planning evidence. (AC: 2)
  - [x] Confirm Phase 51 controlled activation evidence artifacts exist and are prerequisites only.
  - [x] Confirm Epic 132 split-deployment/remote Postgres readiness artifacts exist and are prerequisites only.
  - [x] Confirm Epic 133 DB mTLS readiness artifacts exist and are prerequisites only.
- [x] Implement fail-closed preflight evidence. (AC: 1, 3, 7, 10)
  - [x] Missing operator inputs record blocked/no-go/fail-closed evidence without live smoke activity.
  - [x] The checker requires every operator-gate field before live smoke could be considered.
- [x] Record blocked split deployment smoke evidence outcome under the gate. (AC: 4, 7, 9)
  - [x] Service placement, network boundary, authority, ingress, health/readiness, rollback trigger, and post-smoke decision domains are present as blocked/not-run until operator gate.
- [x] Record blocked remote Postgres smoke/migration evidence outcome under the gate. (AC: 5, 7, 9)
  - [x] Backup checkpoint, single migration runner, bounded pool, writer/read-side, endpoint identity, rollback/fix-forward, and no-plaintext-fallback domains are present as blocked/not-run until operator gate.
- [x] Record blocked DB mTLS smoke/failure evidence outcome under the gate. (AC: 6, 7, 9)
  - [x] TLS/client-cert enforcement, gate state, certificate metadata, no-plaintext fallback, and bounded failure diagnostics domains are present as blocked/not-run until operator gate.
- [x] Update story/status evidence without overclaiming. (AC: 8, 9, 10)
  - [x] Update `docs/feature-status.md` and `_bmad-output/implementation-artifacts/sprint-status.yaml` to reflect the Story 135.1 blocked/no-go/fail-closed outcome.
  - [x] Add and run the Story 135.1 checker plus existing relevant activation/readiness checkers.

## Dev Notes

- Treat this as the separate activation story required by Story 134.6. Do not perform live activation unless the operator gate exists and is explicit.
- A blocked/no-go/fail-closed result is the correct local outcome because operator approval, target details, credentials/certs, and redaction-safe evidence are unavailable.
- Do not commit credential values, token values, private key material, certificate bodies, unredacted DSNs, production host secrets, or full secret paths.
- Preserve local SQLite/default behavior and existing opt-in profile boundaries.
- Prefer extending the existing evidence/checker contracts before adding new mechanisms.
- Keep any implementation bounded to this story. Avoid dependency/lockfile changes unless explicitly justified and required.

### Project Structure Notes

- Planning source: `_bmad-output/planning-artifacts/phase-52-operator-gated-activation-epics.md`.
- Story artifact: `_bmad-output/implementation-artifacts/135-1-operator-gated-split-remote-postgres-db-mtls-activation-smoke.md`.
- New fail-closed contract: `docs/operator-gated-activation-smoke-evidence.json`.
- New local gate: `scripts/check_operator_gated_activation_smoke.py`.
- Status files: `_bmad-output/implementation-artifacts/sprint-status.yaml`, `docs/feature-status.md`.

### References

- `_bmad-output/implementation-artifacts/134-6-controlled-activation-closure-go-no-go-evidence.md` - requires a separate approved activation story with operator-supplied evidence.
- `_bmad-output/planning-artifacts/phase-51-controlled-activation-epics.md` - defines Phase 51 controlled activation evidence requirements and no-activation boundary.
- `docs/controlled-activation-evidence.json` and `scripts/check_controlled_activation_evidence.py` - controlled activation evidence schema/preflight gate.
- `docs/split-deployment-activation-smoke-evidence.json` and `scripts/check_split_deployment_activation_smoke_evidence.py` - split deployment smoke evidence contract.
- `docs/remote-postgres-activation-smoke-migration-evidence.json` and `scripts/check_remote_postgres_activation_smoke_migration_evidence.py` - remote Postgres smoke/migration evidence contract.
- `docs/registry-db-mtls-activation-smoke-failure-evidence.json` and `scripts/check_registry_db_mtls_activation_smoke_failure_evidence.py` - registry DB mTLS smoke/failure evidence contract.
- `docs/combined-split-remote-postgres-db-mtls-rehearsal-evidence.json` and `scripts/check_combined_split_remote_postgres_db_mtls_rehearsal_evidence.py` - combined rehearsal evidence contract.
- `docs/split-deployment-remote-postgres-closure-readiness.json` - Epic 132 readiness closure, prerequisite only.
- `docs/db-mtls-readiness.json` - Epic 133 DB mTLS readiness, prerequisite only.

## Dev Agent Record

### Agent Model Used

Codex / GPT-5.5 via Autopilot (deep-interview -> ralplan -> ultragoal -> code-review -> ultraqa).

### Debug Log References

- Deep-interview handoff: `.omx/interviews/story-135-1-deep-interview-handoff.md`.
- Ralplan plan: `.omx/specs/story-135-1-operator-gated-activation-smoke-plan.md`.
- Architect consensus: `.omx/specs/story-135-1-architect-review.md`.
- Critic consensus: `.omx/specs/story-135-1-critic-review.md`.
- Code review: `.omx/artifacts/code-review/story-135-1-code-review.md` (APPROVE/CLEAR).
- UltraQA: `.omx/artifacts/ultraqa/story-135-1-ultraqa.md` (PASS).

### Completion Notes List

- Added `docs/operator-gated-activation-smoke-evidence.json` to record the Story 135.1 blocked/no-go/fail-closed evidence contract.
- Added `scripts/check_operator_gated_activation_smoke.py` and wired it into `justfile` and CI check/self-test surfaces.
- Updated `docs/feature-status.md` and sprint status to mark only the scoped repo-local fail-closed/no-go outcome done.
- Actual split deployment / remote Postgres / DB mTLS smoke remains blocked pending operator approval, security approval, target environment/version, change window, rollback and emergency-disable ownership, independent review, redaction plan, and sanitized credential/certificate references.

### Verification Evidence

- `uv run ruff format scripts/check_operator_gated_activation_smoke.py` — passed; checker formatted.
- `uv run ruff check scripts/check_operator_gated_activation_smoke.py` — passed.
- `python3 -m py_compile scripts/check_operator_gated_activation_smoke.py` — passed.
- `uv run python scripts/check_operator_gated_activation_smoke.py` — passed; Story 135.1 fail-closed evidence OK.
- `uv run python scripts/check_operator_gated_activation_smoke.py --self-test` — passed; adversarial fixtures reject overclaims, mixed safe/unsafe punctuation-clause bypasses, trailing-disclaimer overclaims, secret-like material, missing required gate/domain fields, and false activation-boundary flags.
- Existing prerequisite/status gates passed: `scripts/check_controlled_activation_evidence.py`, `scripts/check_split_deployment_activation_smoke_evidence.py`, `scripts/check_remote_postgres_activation_smoke_migration_evidence.py`, `scripts/check_registry_db_mtls_activation_smoke_failure_evidence.py`, `scripts/check_combined_split_remote_postgres_db_mtls_rehearsal_evidence.py`, `scripts/check_split_deployment_remote_postgres_closure.py`, and `scripts/check_db_mtls_readiness.py`.
- `just check-gates` — passed, including the new Story 135.1 checker.
- `just check-gates-self-test` — passed, including the new Story 135.1 checker self-test.
- `uv run secret-hygiene-precommit docs/operator-gated-activation-smoke-evidence.json scripts/check_operator_gated_activation_smoke.py` — passed for new untracked files; emitted existing `scancode-toolkit not installed; license scan skipped` warnings and exited successfully.
- `just lint` — passed; ruff, mypy, status/readiness gates, and full tracked-file secret hygiene completed. Secret-hygiene emitted existing `scancode-toolkit not installed; license scan skipped` warnings but exited successfully.
- `git diff --check` — passed.
- No live activation, deployment, provisioning, migration execution, credential/certificate handling, activation runtime/deployment config change, dependency/lockfile change, compose profile activation, plaintext fallback, or production-state change was run during verification.

### File List

- `docs/operator-gated-activation-smoke-evidence.json`
- `scripts/check_operator_gated_activation_smoke.py`
- `justfile`
- `.github/workflows/ci.yml`
- `docs/feature-status.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/135-1-operator-gated-split-remote-postgres-db-mtls-activation-smoke.md`
