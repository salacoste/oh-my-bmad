# Phase 17 Epics — Destructive Lifecycle Apply Readiness

Phase 17 converts the remaining Phase 15 lifecycle future candidate into a **planning/readiness** phase. It does not implement destructive apply.

## Epic 81 — Planning and scope lock

### Story 81.1: Phase 17 planning/status artifacts

- Status: done in this planning pass.
- Scope: PRD amendment, architecture amendment, epics/story scope, acceptance criteria, non-goals, allowed/forbidden write set.
- Acceptance: Phase 17 is explicitly planning/readiness only and preserves ADR-0025.

## Epic 82 — Apply precondition contract

### Story 82.1: Plan-hash authorization contract

- Status: done in Story 82.1.
- Scope: define future authorization evidence for exact dry-run `plan_hash`, affected segment identities, replay/rollback evidence references, operator identity, and re-computation before mutation.
- Acceptance: docs specify fail-closed behavior for missing, stale, unsigned, unverifiable, or mismatched authorization; future apply cannot be enabled by a dry-run boolean toggle.
- Artifact: `_bmad-output/implementation-artifacts/82-1-plan-hash-authorization-contract.md`.

## Epic 83 — Replay and rollback proof contract

### Story 83.1: Replay-validation and rollback evidence contract

- Status: done in Story 83.1.
- Scope: define future replay validation proof and backup/restore evidence requirements for retained hot+archive event sets and every affected hot segment.
- Acceptance: docs specify apply is blocked when replay validation, archive manifest validation, backup artifacts, restore instructions, restore drill evidence, or rollback coverage is absent, stale, failed, ambiguous, or unverifiable. Operator acknowledgement alone is only a bounded risk-acceptance exception if a future implementation story defines expiry, reviewer identity, rationale, and affected segment scope.
- Artifact: `_bmad-output/implementation-artifacts/83-1-replay-validation-rollback-evidence-contract.md`.

## Epic 84 — Documentation reconciliation and static guard

### Story 84.1: Phase 17 docs/status reconciliation

- Scope: update README/project overview/API/operator/data/architecture docs to close Phase 16 and open Phase 17 planning/readiness only.
- Acceptance: docs preserve that destructive apply remains unimplemented and future-gated.

## Epic 85 — Final verification and release hygiene

### Story 85.1: Quality gate, review, commit, push, CI

- Scope: targeted tests/static checks, ai-slop-cleaner, independent code-reviewer and architect review, commit/push, CI verification, final Ultragoal checkpoint.
- Acceptance: final recommendation APPROVE, architect status CLEAR, CI green.
