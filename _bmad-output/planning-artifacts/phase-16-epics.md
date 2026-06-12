# Phase 16 Epics — Archive-Aware Task History

Phase 16 turns the first Phase 15 future candidate into a small read-only implementation slice. It changes the operator-facing task-history query contract only after separately specifying requirements and tests.

## Epic 76 — Planning and scope lock

### Story 76.1: Phase 16 planning/status artifacts

- Status: done in this planning pass.
- Scope: PRD amendment, architecture amendment, epics/story scope, sprint-status entries, acceptance criteria, non-goals, and allowed/forbidden write set.
- Acceptance: Phase 16 has explicit read-only scope and preserves ADR-0025 destructive-operation boundary.

## Epic 77 — Archive-aware history helper

### Story 77.1: Deterministic merged task-history collection

- Scope: add or adapt a helper that collects hot+archive envelopes through the existing archive manifest contract and filters by `task_id` without mutating state.
- Acceptance: no manifest keeps hot-only behavior; valid manifest includes archive-only task events; ordering is monotonic.

## Epic 78 — Registry API route integration

### Story 78.1: `/v1/tasks/{task_id}/history` archive opt-in

- Scope: route uses the merged helper with existing archive config resolution and route-local ProblemDetails mapping.
- Acceptance: valid archive-only task returns 200; invalid archive config fails closed; 404 remains when neither hot nor archive events match.

## Epic 79 — Documentation reconciliation

### Story 79.1: Operator/API/data/architecture docs update

- Scope: update docs to state task history is archive-aware only when archive manifest config is present, still read-only, and still not a destructive lifecycle apply path.
- Acceptance: docs preserve Phase 14/15 safety language and remove obsolete "future only" wording for archive-aware task history once implementation ships.

## Epic 80 — Final verification and release hygiene

### Story 80.1: Quality gate, review, commit, push, CI

- Scope: targeted tests, static checks, ai-slop-cleaner, independent code-reviewer and architect review, commit/push, CI verification, Ultragoal final checkpoint.
- Acceptance: final recommendation APPROVE, architect status CLEAR, CI green.
