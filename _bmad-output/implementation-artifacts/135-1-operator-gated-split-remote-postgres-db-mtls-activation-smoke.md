# Story 135.1: Operator-Gated Split Deployment / Remote Postgres / DB mTLS Activation Smoke

Status: ready-for-dev

## Story

As an operator,
I want a single operator-gated activation-smoke story for split deployment, remote Postgres, and DB mTLS,
so that real activation evidence is collected only under an approved change window with rollback and emergency-disable ownership.

## Activation Boundary

This story is an implementation-ready work item, not evidence that activation occurred. It may proceed to live smoke activity only after explicit operator approval, security approval, target environment/version, change window, rollback owner/plan, emergency-disable owner/plan, independent reviewer, redaction plan, and credential/certificate reference boundaries are supplied outside the repository.

If those prerequisites are absent, stale, or incomplete, implementation must fail closed and record a blocked/no-go or repo-local gate outcome. No live split deployment activation, remote Postgres activation, DB mTLS production activation, live database cutover, migration execution, provisioning, production host mutation, credential/certificate handling, runtime/deployment config change, dependency/lock change, plaintext fallback, or production-state change is authorized by merely opening this story.

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

- [ ] Validate operator gate inputs before any live-smoke step. (AC: 1, 3, 7)
  - [ ] Record operator approval reference, security approval reference, target environment and version, and change window.
  - [ ] Record rollback owner/plan, emergency-disable owner/plan, independent reviewer, and evidence retention/freshness rules.
  - [ ] Confirm redaction plan and approved secret/certificate reference boundaries; do not write secret values to repo artifacts.
- [ ] Reconcile prerequisite readiness/planning evidence. (AC: 2)
  - [ ] Confirm Phase 51 controlled activation evidence artifacts exist and are prerequisites only.
  - [ ] Confirm Epic 132 split-deployment/remote Postgres readiness artifacts exist and are prerequisites only.
  - [ ] Confirm Epic 133 DB mTLS readiness artifacts exist and are prerequisites only.
- [ ] Implement or collect fail-closed preflight evidence. (AC: 1, 3, 7, 10)
  - [ ] If operator inputs are missing, record blocked/no-go/fail-closed evidence without live smoke activity.
  - [ ] If operator inputs are present, verify every required field before allowing the bounded smoke path.
- [ ] Collect split deployment smoke evidence under the gate. (AC: 4, 7, 9)
  - [ ] Capture sanitized service placement, network boundary, authority, ingress, health/readiness, rollback trigger, and post-smoke decision evidence.
- [ ] Collect remote Postgres smoke/migration evidence under the gate. (AC: 5, 7, 9)
  - [ ] Capture sanitized backup checkpoint, single migration runner, bounded pool, writer/read-side, endpoint identity, and rollback/fix-forward evidence.
- [ ] Collect DB mTLS smoke/failure evidence under the gate. (AC: 6, 7, 9)
  - [ ] Capture sanitized TLS/client-cert enforcement, gate state, certificate metadata references, no-plaintext fallback, and bounded failure diagnostics.
- [ ] Update story/status evidence without overclaiming. (AC: 8, 9, 10)
  - [ ] Update `docs/feature-status.md` and `_bmad-output/implementation-artifacts/sprint-status.yaml` to reflect the actual Story 135.1 outcome.
  - [ ] Run existing activation/readiness checkers and any new focused checks introduced by the story.

## Dev Notes

- Treat this as the separate activation story required by Story 134.6. Do not perform live activation unless the operator gate exists and is explicit.
- A blocked/no-go/fail-closed result is acceptable if operator approval, target details, credentials/certs, or redaction-safe evidence are unavailable.
- Do not commit credential values, token values, private key material, certificate bodies, unredacted DSNs, production host secrets, or full secret paths.
- Preserve local SQLite/default behavior and existing opt-in profile boundaries.
- Prefer extending the existing evidence/checker contracts before adding new mechanisms.
- Keep any implementation bounded to this story. Avoid dependency/lockfile changes unless explicitly justified and required.

### Project Structure Notes

- Planning source: `_bmad-output/planning-artifacts/phase-52-operator-gated-activation-epics.md`.
- Story artifact: `_bmad-output/implementation-artifacts/135-1-operator-gated-split-remote-postgres-db-mtls-activation-smoke.md`.
- Status files: `_bmad-output/implementation-artifacts/sprint-status.yaml`, `docs/feature-status.md`.
- Existing evidence schemas/checkers should remain the first reuse target before creating new artifacts.

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

TBD by dev-story

### Debug Log References

### Completion Notes List

### File List
