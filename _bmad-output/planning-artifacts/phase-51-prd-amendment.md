# Phase 51 PRD Amendment — Controlled Production Activation Evidence Planning

Generated: 2026-07-08T00:00:00+03:00

## Scope statement

Phase 51 opens Epic 134 as a docs/status/planning-only track for future controlled production activation evidence. It does not activate production, provision infrastructure, mutate hosts, execute migrations, enable compose profiles, load credentials, install certificate material, or change runtime code.

Phase 50 / Epic 133 is complete locally as DB connection mTLS readiness: registry-state DB mTLS support is runtime-gated and documented, while production activation remains operator-gated. Phase 51 plans the evidence that a future operator-run activation story must collect before any claim that split deployment, remote Postgres, or DB mTLS was activated.

## Product decision

Open **Epic 134 — Controlled production activation evidence planning** with six future/operator-gated stories:

1. **Story 134.1: Activation Evidence Schema and Preflight Gate**
2. **Story 134.2: Split Deployment Activation Smoke Evidence Package**
3. **Story 134.3: Remote Postgres Activation Smoke and Migration Evidence Package**
4. **Story 134.4: Registry DB mTLS Activation Smoke and Failure Evidence Package**
5. **Story 134.5: Combined Split + Remote Postgres + DB mTLS Rehearsal**
6. **Story 134.6: Controlled Activation Closure and Go/No-Go Evidence**

## Product goals

- Define the minimum evidence required before any future activation story may claim production activation occurred.
- Keep local/default behavior unchanged until an operator explicitly performs a separate activation run outside this planning patch.
- Separate readiness evidence from activation evidence, so Phase 50 local DB mTLS readiness is not mistaken for live production activation.
- Require future activation proof for split deployment, remote Postgres, and DB mTLS smoke checks to be operator-gated, secret-free, redacted, timestamped, reversible, and tied to an explicit change window.

## Non-goals

Phase 51 does not perform or authorize live production mutation, provisioning, production host mutation, migration execution, compose/profile production activation, credential creation, credential display, certificate/key material storage, plaintext fallback, dependency changes, lockfile edits, script edits, runtime code edits, deployment config edits, or production-state changes.

## Evidence principles

Future activation evidence must be:

- **Operator-gated:** tied to an explicit operator approval, change window, target environment, rollback owner, and emergency-disable path.
- **Secret-free:** no credential values, private key material, certificate contents, unredacted connection strings, or token-like strings in docs/status artifacts.
- **Activation-specific:** readiness artifacts can be prerequisites, but they are not proof that activation occurred.
- **Reversible:** every activation evidence packet must name rollback or fail-closed handling before the activation is attempted.
- **Smoke-bounded:** split deployment, remote Postgres, and DB mTLS smoke evidence must be bounded to health/readiness/metadata checks and sanitized diagnostics; it must not include uncontrolled load, destructive restore, backup pruning, plaintext fallback, or broad mutation.

## Acceptance criteria

1. Phase 51 planning artifacts define Epic 134 and Stories 134.1-134.6 as future/operator-gated activation evidence only.
2. Sprint status records Phase 50 / Epic 133 as complete locally and Phase 51 / Epic 134 as open planning.
3. Docs distinguish readiness from future activation for split deployment, remote Postgres, and DB mTLS smoke evidence.
4. No production activation, provisioning, host mutation, migration execution, credential/cert material, compose/profile activation, plaintext fallback, runtime code, deployment config, scripts, locks, dependencies, or production state are changed by this patch.
