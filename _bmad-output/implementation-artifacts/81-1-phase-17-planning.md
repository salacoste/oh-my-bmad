# Story 81.1: Phase 17 planning/status artifacts

## Status

Done in planning pass.

## Story

As an operator and maintainer,
I want Phase 17 to define destructive lifecycle apply readiness before any destructive implementation exists,
so that future disk-reclamation work cannot weaken replay correctness, auditability, or ADR-0025 safety gates.

## Acceptance criteria

1. Phase 17 PRD amendment exists and defines FR156-FR161 plus non-goals.
2. Phase 17 architecture amendment exists and defines invariants, allowed write set, forbidden write set, and verification strategy.
3. Phase 17 epics exist and decompose readiness into Epics 81-85.
4. Sprint status registers Phase 17 and marks Story 81.1 done while later readiness/reconciliation/release stories remain backlog.
5. No runtime/package/API/deployment behavior is changed by this story.

## Evidence

- `_bmad-output/planning-artifacts/phase-17-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-17-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-17-epics.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
