# Phase 17 Epics — Destructive Lifecycle Apply Readiness

Phase 17 converts the remaining Phase 15 lifecycle future candidate into a **planning/readiness** phase. It does not implement destructive apply.

## Epic 81 — Planning and scope lock

### Story 81.1: Phase 17 planning/status artifacts

- Status: done in this planning pass.
- Scope: PRD amendment, architecture amendment, epics/story scope, acceptance criteria, non-goals, allowed/forbidden write set.
- Acceptance: Phase 17 is explicitly planning/readiness only and preserves ADR-0025.

## Epic 82 — Apply precondition contract

### Story 82.1: Plan-hash authorization contract

- Scope: define future authorization evidence for exact dry-run `plan_hash`, affected segment identities, and re-computation before mutation.
- Acceptance: docs specify fail-closed behavior for plan-hash mismatch or missing authorization.

## Epic 83 — Replay and rollback proof contract

### Story 83.1: Replay-validation and rollback evidence contract

- Scope: define future replay validation proof and backup/restore evidence requirements for affected hot segments.
- Acceptance: docs specify apply is blocked when replay validation, archive manifest validation, or rollback evidence is absent/ambiguous.

## Epic 84 — Documentation reconciliation and static guard

### Story 84.1: Phase 17 docs/status reconciliation

- Scope: update README/project overview/API/operator/data/architecture docs to close Phase 16 and open Phase 17 planning/readiness only.
- Acceptance: docs preserve that destructive apply remains unimplemented and future-gated.

## Epic 85 — Final verification and release hygiene

### Story 85.1: Quality gate, review, commit, push, CI

- Scope: targeted tests/static checks, ai-slop-cleaner, independent code-reviewer and architect review, commit/push, CI verification, final Ultragoal checkpoint.
- Acceptance: final recommendation APPROVE, architect status CLEAR, CI green.
