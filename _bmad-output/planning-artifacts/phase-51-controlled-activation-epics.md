---
stepsCompleted:
  - step-01-validate-prerequisites
  - step-02-design-epic
  - step-03-create-stories
  - step-04-final-validation
workflowStatus: 'complete'
workflowType: 'epics-and-stories'
project_name: 'oh-my-bmad'
user_name: 'R2d2'
date: '2026-07-08'
phase: 51
finalEpicCount: 1
finalStoryCount: 6
inputDocuments:
  - _bmad-output/planning-artifacts/phase-48-production-readiness-epics.md
  - _bmad-output/implementation-artifacts/sprint-status.yaml
  - docs/feature-status.md
  - docs/production-operations.md
  - docs/split-deployment-remote-postgres-closure-readiness.json
  - docs/db-mtls-readiness.json
sourceNotes:
  - 'Generated as a docs/status/planning-only Phase 51 patch.'
  - 'This artifact plans future/operator-gated activation evidence only; it does not activate production or mutate runtime/deployment state.'
---

# oh-my-bmad — Phase 51 Controlled Activation Evidence Epic and Story Breakdown

## Overview

Phase 51 opens Epic 134 to define evidence requirements for a future controlled production activation. The planning scope is intentionally narrower than activation: it records what future operator-gated stories must prove before anyone can claim split deployment, remote Postgres, or DB mTLS activation occurred.

Phase 50 / Epic 133 DB mTLS readiness is complete locally. Epic 132 split-deployment and remote Postgres readiness is complete locally. Those readiness states remain prerequisites, not live-activation proof.

## Requirements inventory

### Functional requirements

- **FR417:** The operator has a secret-free activation evidence schema and preflight gate that separates readiness, approval, execution, smoke evidence, rollback, and closure.
- **FR418:** Future split-deployment activation evidence proves service placement, network boundary, single-writer/event-log authority, health/readiness, and rollback without hidden production mutation.
- **FR419:** Future remote Postgres activation evidence proves migration prerequisites, backup checkpoint, bounded pooling, writer authority, redacted connection metadata, and rollback/fix-forward criteria.
- **FR420:** Future DB mTLS smoke evidence proves explicit gate enablement, server/client certificate enforcement, sanitized certificate lifecycle metadata, no plaintext fallback, and bounded failure diagnostics.
- **FR421:** Future combined split deployment, remote Postgres, and DB mTLS rehearsal evidence is captured before activation is attempted and reconciled after smoke results.
- **FR422:** Controlled activation closure and go/no-go evidence reconciles readiness vs activation language and blocks activation overclaim until future operator evidence exists.

### Nonfunctional requirements

- **NFR-P51-1:** Evidence records are concise, timestamped, bounded, and redacted.
- **NFR-R51-1:** Failed or incomplete activation evidence leaves status fail-closed or rollback-required, never silently active.
- **NFR-S51-1:** Evidence artifacts contain no credential values, private key material, real certificate contents, or unredacted connection strings.
- **NFR-M51-1:** Phase 51 changes remain docs/status/planning-only and do not touch runtime code, deployment configs, scripts, credentials, locks, dependencies, or production state.

## Epic 134: Controlled Production Activation Evidence Planning

Goal: define the future/operator-gated evidence packet for controlled production activation so readiness cannot be confused with live activation and activation cannot be claimed without redacted, reversible, operator-approved proof.

### Story 134.1: Activation Evidence Schema and Preflight Gate

As the operator, I want a single activation evidence schema and preflight gate, so future activation stories can prove who approved what, for which target, during which change window, with which rollback owner.

**Acceptance criteria:**

- Defines required evidence fields: target environment, operator approval reference, change window, readiness prerequisites, activation intent, smoke scope, rollback owner, emergency-disable owner, evidence retention, and redaction statement.
- States readiness artifacts are prerequisites only, not proof activation occurred.
- Requires future evidence to remain secret-free and free of certificate/key material.
- Does not modify runtime code, scripts, deployment configs, credentials, locks, dependencies, or production state.

### Story 134.2: Split Deployment Activation Smoke Evidence Package

As the operator, I want split-deployment activation evidence planned before any split rollout, so service placement and authority boundaries are proven before future activation.

**Acceptance criteria:**

- Defines future proof for service placement, network boundaries, registry-state single-writer authority, event-log append authority, MCP boundary, operator/dashboard ingress boundary, health/readiness, and rollback.
- Requires evidence to be operator-gated and timestamped.
- States no live split activation, external load-balancer activation, host-port change, production host mutation, or compose/profile activation occurs in this planning story.

### Story 134.3: Remote Postgres Activation Smoke and Migration Evidence Package

As the operator, I want remote Postgres activation evidence planned before any production database cutover, so migration and rollback prerequisites are explicit.

**Acceptance criteria:**

- Defines future proof for migration preconditions, single migration runner, backup/restore checkpoint, bounded pool settings, writer authority, read-side compatibility, redacted database endpoint identity, and rollback/fix-forward criteria.
- Requires redacted evidence only; credential values and unredacted connection strings are forbidden.
- States no provisioning, migration execution, production host mutation, live database cutover, compose/profile activation, or plaintext fallback occurs in this planning story.

### Story 134.4: Registry DB mTLS Activation Smoke and Failure Evidence Package

As the operator, I want DB mTLS smoke evidence planned before enabling it in production, so certificate enforcement and no-plaintext behavior are proven without exposing secrets.

**Acceptance criteria:**

- Defines future proof for explicit operator gate enablement, server-side TLS/client-cert enforcement, approved secret locations by identifier only, expiry/revocation/hostname metadata, no-plaintext fallback behavior, and bounded sanitized failure diagnostics.
- Requires smoke evidence to be redacted, timestamped, and tied to rollback/fail-closed criteria.
- States no real certificate material, private key material, credential values, production activation, provisioning, host mutation, compose/profile activation, or plaintext fallback occurs in this planning story.

### Story 134.5: Combined Split + Remote Postgres + DB mTLS Rehearsal

As the operator, I want a combined split deployment, remote Postgres, and DB mTLS rehearsal planned before activation, so failed or incomplete activation cannot leave ambiguous production state.

**Acceptance criteria:**

- Defines future proof for combined split-deployment placement, remote Postgres migration/smoke readiness, registry DB mTLS smoke/failure diagnostics, rollback trigger, rollback owner, emergency-disable mechanism, pre-activation backup checkpoint, post-smoke decision, degraded-state handling, and operator signoff.
- Requires failed or incomplete activation evidence to leave status fail-closed or rollback-required.
- States no rollback execution, restore execution, destructive operation, production host mutation, credential use, script change, or production-state change occurs in this planning story.

### Story 134.6: Controlled Activation Closure and Go/No-Go Evidence

As the operator, I want controlled activation closure and go/no-go evidence only after docs/status language is reconciled, so future readers can see that activation evidence is planned but not executed.

**Acceptance criteria:**

- Reconciles sprint status and human docs to mark Phase 51 as planning/open until future operator-gated activation stories exist.
- Verifies Phase 50 / Epic 133 local DB mTLS readiness remains complete without claiming production activation.
- Verifies split deployment, remote Postgres, and DB mTLS smoke evidence are described as future/operator-gated planning only.
- Confirms no live activation, provisioning, production host mutation, migration execution, compose/profile production activation, credentials, cert material, plaintext fallback, runtime code, scripts, deployment config, dependencies, locks, or production state changed in Phase 51 planning.
