# Phase 51 Architecture Amendment — Controlled Activation Evidence Boundary

Generated: 2026-07-08T00:00:00+03:00

## Canonical decision

Phase 51 is an architecture boundary for future activation evidence, not activation. The complete story sequence lives in `phase-51-controlled-activation-epics.md`.

## Baseline

Phase 50 / Epic 133 closes local DB connection mTLS readiness with runtime-gated registry-state support and executable readiness checks. Epic 132 split-deployment and remote Postgres readiness artifacts are also local readiness contracts. None of those artifacts prove live activation, provisioning, production host mutation, migration execution, compose/profile activation, real certificate deployment, or production smoke success.

## Architecture boundary

Future activation stories must treat these as separate layers:

1. **Readiness layer:** existing docs, checkers, tests, and closure artifacts that prove the repository can support an activation path.
2. **Operator authorization layer:** explicit approval, change window, target environment, rollback owner, emergency-disable owner, and evidence retention decision.
3. **Activation execution layer:** future operator-run steps outside this planning patch.
4. **Evidence layer:** sanitized records proving what was attempted, what succeeded or failed, and how rollback/fail-closed behavior was preserved.

Phase 51 only defines layer 4 requirements and the preconditions for future layer 2/3 stories. It does not modify runtime code, scripts, deployment files, credentials, locks, dependencies, or production state.

## Required future evidence domains

### Split deployment

Future split-deployment activation evidence must prove the intended service placement, network boundary, single-writer authority, event-log append authority, operator ingress boundary, health/readiness status, and rollback path. It must not rely on hidden host mutation, uncontrolled external load balancers, undeclared host ports, broad dashboard control surfaces, or unrecorded profile activation.

### Remote Postgres

Future remote Postgres activation evidence must prove migration preconditions, single migration runner authority, backup/restore checkpoint existence, bounded pool settings, registry-api read-side compatibility, registry-state writer authority, redacted connection metadata, and rollback/fix-forward decision criteria. It must not include credential values, live provisioning by this patch, migration execution by this patch, production host mutation, or plaintext fallback.

### DB mTLS smoke evidence

Future DB mTLS smoke evidence must prove that the mTLS gate is explicitly enabled only by the operator, server-side TLS/client-cert requirements are enforced, certificate expiry/revocation/hostname checks are represented by sanitized metadata, plaintext fallback is rejected, and failures are bounded and redacted. Evidence must never include private key material, real certificate contents, unredacted database connection strings, or token values.

## Fail-closed invariants

- Missing, stale, malformed, self-attested, or secret-bearing evidence cannot satisfy activation evidence.
- Readiness closure is not activation proof.
- A future failed smoke check must leave the platform in fail-closed or rollback-required status, not silently continue on plaintext or degraded production settings.
- Local SQLite/default behavior remains the baseline until a future operator-gated activation story records otherwise.

## Deferred until future implementation stories

Activation execution, production provisioning, host mutation, migration execution, compose/profile production activation, credential/certificate installation, external smoke runs, dashboards/commands for production activation, script changes, runtime code changes, CI/deployment changes, dependencies, lockfiles, and production-state changes remain deferred.
