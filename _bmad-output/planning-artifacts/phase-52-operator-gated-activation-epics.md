---
stepsCompleted:
  - step-01-validate-prerequisites
  - step-02-design-epic
  - step-03-create-story
  - step-04-final-validation-pending-dev-story
workflowStatus: 'story-opened'
workflowType: 'epics-and-stories'
project_name: 'oh-my-bmad'
user_name: 'R2d2'
date: '2026-07-12'
phase: 52
finalEpicCount: 1
finalStoryCount: 1
inputDocuments:
  - _bmad-output/planning-artifacts/phase-51-prd-amendment.md
  - _bmad-output/planning-artifacts/phase-51-architecture-amendment.md
  - _bmad-output/planning-artifacts/phase-51-controlled-activation-epics.md
  - _bmad-output/implementation-artifacts/134-6-controlled-activation-closure-go-no-go-evidence.md
  - _bmad-output/implementation-artifacts/sprint-status.yaml
  - docs/feature-status.md
sourceNotes:
  - 'Generated to open the separate future/operator-gated activation story requested after Phase 51 closure.'
  - 'This artifact opens an implementation-ready story only; it does not perform live activation, provisioning, migration execution, credential handling, deployment mutation, or production-state change.'
---

# oh-my-bmad - Phase 52 Operator-Gated Activation Epic and Story Breakdown

## Overview

Phase 52 opens Epic 135 as the separate operator-gated activation work item that Phase 51 deliberately deferred. Epic 134 remains closed as planning-only/docs-status evidence, not activation. Story 135.1 is ready for a future `dev-story` pass to collect or implement the evidence workflow for a real split deployment / remote Postgres / DB mTLS smoke activation only when operator approval, target environment, change window, rollback ownership, emergency-disable ownership, and redacted evidence inputs exist.

Opening this story does not authorize live activation by itself. If the required operator gate is absent, Story 135.1 must fail closed and may only add repo-local gate/checker/status evidence that explains why no live activation was attempted.

## Requirements inventory

### Functional requirements

- **FR423:** A separate operator-gated activation story exists after Phase 51 closure so readiness/planning evidence cannot be mistaken for live activation evidence.
- **FR424:** The story requires explicit approval references, security approval, target environment and version, change window, rollback owner/plan, emergency-disable owner/plan, independent reviewer, and evidence freshness before any activation smoke can be attempted.
- **FR425:** Split deployment evidence must prove service placement, network boundaries, single-writer/event-log authority, MCP boundary, operator/dashboard ingress, health/readiness, and rollback decision points using sanitized output.
- **FR426:** Remote Postgres evidence must prove backup checkpoint, single migration runner authority, bounded pool settings, migration/rollback/fix-forward decision points, redacted database identity, and no plaintext fallback.
- **FR427:** DB mTLS evidence must prove explicit gate enablement, server-side TLS and client certificate enforcement, certificate metadata by reference only, no plaintext fallback, and sanitized failure diagnostics.
- **FR428:** The story must preserve a fail-closed path when approvals, target details, credentials/certs, or smoke evidence are unavailable.

### Nonfunctional requirements

- **NFR-P52-1:** Evidence is timestamped, bounded, auditable, independently reviewed, and tied to a specific target environment and change window.
- **NFR-R52-1:** Failed, partial, missing, or stale evidence leaves status blocked/fail-closed or rollback-required; it never implies activation occurred.
- **NFR-S52-1:** Evidence artifacts contain no credential values, token values, private key material, certificate bodies, unredacted DSNs, production host secrets, or full secret paths.
- **NFR-M52-1:** Opening Story 135.1 does not change runtime code, deployment configs, scripts, credentials, locks, dependencies, or production state. Any future implementation must stay inside the approved story scope and operator gate.

## Epic 135: Operator-Gated Split Deployment / Remote Postgres / DB mTLS Activation Smoke

Goal: make the first real activation-smoke work item explicit, gated, reversible, and evidence-driven so live activation is never inferred from readiness artifacts or planning-only closures.

### Story 135.1: Operator-Gated Split Deployment / Remote Postgres / DB mTLS Activation Smoke

As the operator, I want a single operator-gated activation-smoke story for split deployment, remote Postgres, and DB mTLS, so activation evidence can be collected only under an approved change window with rollback and emergency-disable ownership.

**Acceptance criteria:**

- Requires explicit operator approval, security approval, target environment and version, change window, rollback owner and plan, emergency-disable owner and plan, independent reviewer, evidence retention/freshness, and redaction statement before any activation smoke can be attempted.
- Uses Phase 51 controlled activation evidence schemas and existing Epic 132/Epic 133 readiness artifacts as prerequisites only; readiness artifacts are not proof activation occurred.
- Fails closed without running activation smoke if any approval, target, rollback, emergency-disable, secret reference, or evidence-redaction prerequisite is missing or stale.
- Records split deployment evidence for service placement, network boundary, registry-state single-writer authority, event-log append authority, MCP boundary, operator/dashboard ingress, health/readiness, rollback trigger, and post-smoke go/no-go decision.
- Records remote Postgres evidence for backup checkpoint, single migration runner authority, bounded pool settings, migration/rollback/fix-forward decision points, read-side compatibility, writer authority, redacted endpoint identity, and no plaintext fallback.
- Records DB mTLS evidence for explicit `REGISTRY_DB_MTLS_ENABLED` gate state, server-side TLS/client certificate enforcement, approved secret references by identifier only, certificate expiry/revocation/hostname metadata, bounded failure diagnostics, and no plaintext fallback.
- Saves only sanitized artifacts; credential values, token values, private key material, certificate bodies, unredacted DSNs, production host secrets, and full secret paths are forbidden.
- Does not claim activation from planning/readiness evidence. If live evidence cannot be supplied under the operator gate, the story records a blocked/fail-closed outcome and no live activation claim.
- Any code/script/deployment/runtime change requires explicit scope inside Story 135.1 and must preserve local SQLite/default behavior. Unscoped changes to dependencies, locks, broad runtime behavior, CI, or production state are forbidden.
