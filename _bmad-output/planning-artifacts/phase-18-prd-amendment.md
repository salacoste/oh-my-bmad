# Phase 18 PRD Amendment — Destructive Lifecycle Apply Product Scope (P18-DLAPS)

## Summary

Phase 18 starts the explicitly authorized BMAD planning path for **destructive event-log lifecycle apply**. This PRD amendment defines the product scope, operator authority model, safety gates, non-goals, and acceptance criteria for a future implementation plan.

This artifact does **not** implement apply/prune and does **not** authorize runtime mutation code by itself. It is the product requirements gate required by the Phase 17 closure route before architecture, test design, implementation stories, and independent security/architecture review can proceed.

## Problem

Phase 14 shipped non-destructive lifecycle dry-run planning under ADR-0025. Phase 16 made task history archive-aware. Phase 17 formalized future apply readiness: exact dry-run `plan_hash` binding, replay validation, rollback evidence, distinct apply surface, and explicit operator gate.

Operators still need a future way to reclaim hot event-log disk space, but the hot event log is part of the audit source of truth. A destructive apply feature must therefore be product-scoped before any implementation is designed or coded.

## Goals

- Define what destructive lifecycle apply is allowed to do and under which operator authority.
- Preserve ADR-0025: dry-run planning and validation are separate from mutation.
- Require exact dry-run plan identity, replay-validation proof, rollback evidence, and durable authorization evidence before mutation.
- Require fail-closed behavior for ambiguous archive coverage, stale plans, missing rollback evidence, invalid authorization, or mismatched affected segments.
- Keep implementation blocked until architecture, tests, and independent review are complete.

## Scope

IN:

- Product requirements for a future destructive lifecycle apply feature.
- Operator authorization requirements and durable evidence fields.
- Preconditions for exact `plan_hash` re-computation, replay validation, rollback evidence, and affected segment identity checks.
- Requirements for a distinct apply surface that cannot be triggered by a `dry_run=false` toggle.
- Fail-closed product behavior and audit/event evidence requirements.
- Documentation/status updates proving that Phase 18 is PRD-only unless later stories explicitly authorize more.

OUT:

- Any runtime implementation that deletes, truncates, moves, rewrites, chmods, prunes, mutates archives, mutates manifests, or mutates hot logs.
- HTTP route, CLI command, MCP tool, worker, cron, scheduled job, object-storage lifecycle job, or filesystem mutation helper for apply/prune.
- Credentialed production operation or real operator execution.
- Relaxing archive checksum validation, route-local archive ProblemDetails, `HOT_ONLY_REPLAY` snapshot boundaries, append-only event-spine assumptions, or dry-run/apply separability.
- Combining dry-run and apply behind a boolean flag.

## Functional requirements

- **FR162 — Phase 18 product-scope gate.** The repository records Phase 18 as the product-scope gate for future destructive lifecycle apply. Phase 18 PRD creation alone does not authorize implementation.
- **FR163 — Operator authority model.** Future apply requires explicit operator authorization tied to the exact dry-run `plan_hash`, dry-run artifact reference, affected segment identities, safety policy version, retention input digest, replay-validation proof reference, rollback-evidence reference, operator identity, authorization timestamp, and durable authorization event or ledger reference.
- **FR164 — Immediate pre-mutation plan verification.** Future apply must regenerate or reload the dry-run inputs immediately before mutation, re-compute the exact `plan_hash`, compare affected segment identities and policy inputs, and fail closed on any mismatch.
- **FR165 — Replay-validation precondition.** Future apply must prove archive manifest validation and replay validation over the retained hot+archive set before mutation. Ambiguous archive coverage, invalid checksums, route-local archive errors, or missing replay proof block apply.
- **FR166 — Rollback evidence precondition.** Future apply must prove backup artifacts outside the hot event-log directory, affected segment checksums/sizes, restore instructions, and restore drill evidence before mutation. A bounded risk-acceptance exception is allowed only if a later implementation story defines scope, rationale, reviewer identity, expiry, affected segment identities, and fail-closed handling.
- **FR167 — Distinct apply surface and audit trail.** Future apply must be a separate operator surface from dry-run, emit durable audit evidence, and provide clear operator-visible summaries of what will be mutated, what was retained, and how rollback can be performed.
- **FR168 — No destructive behavior in this PRD slice.** This PRD slice must not change runtime/package/API/MCP/deployment/CI/dependency behavior or introduce any mutation path.

## Non-functional requirements

- **NFR-S25 — Destructive action fail-closed.** Missing, stale, mismatched, or unverifiable plan, replay, rollback, or authorization evidence blocks apply.
- **NFR-S26 — Least-authority operator gate.** Authorization must be scoped to one exact plan and affected segment set; broad approval cannot authorize mutation.
- **NFR-O23 — Auditable lifecycle apply evidence.** Future apply must produce durable evidence that can be reviewed independently after execution.
- **NFR-M19 — Dry-run/apply separability.** Product, architecture, tests, and implementation must keep planning and mutation as separate surfaces.
- **NFR-R19 — Rollback-first operability.** Future apply is not product-ready unless restore evidence exists before mutation.

## Acceptance criteria

1. Phase 18 PRD amendment defines product scope, operator authority, safety preconditions, non-goals, and forbidden implementation paths.
2. Sprint status records Phase 18 PRD creation as docs/status-only and does not claim destructive apply implementation exists.
3. Verification proves no runtime/package/API/MCP/service/script/deployment/dependency/lockfile/CI path changed.
4. Future work remains blocked until architecture, test-first contracts, implementation stories, and independent security/architecture review are produced.
5. The next non-destructive future-candidate selection is recorded separately and does not weaken the Phase 18 destructive-apply gate.

## Required follow-on gates before implementation

Before any destructive lifecycle apply implementation can be started, BMAD must produce and approve:

1. Phase 18 architecture amendment for exact apply surface, operator gate, replay proof, rollback evidence, and audit events.
2. Epics/stories decomposing the work into test-first fail-closed slices.
3. Acceptance-test design for stale plan rejection, segment mismatch rejection, missing rollback proof rejection, archive validation failure, dry-run/apply separability, and audit evidence.
4. Independent security and architecture reviews before runtime mutation code lands.
5. Final CI and operator evidence requirements for any future apply execution path.
