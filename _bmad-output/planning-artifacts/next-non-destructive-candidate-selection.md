# Next Non-Destructive Candidate Selection

Date: 2026-06-13  
Context: after Phase 17 closure and Phase 18 PRD-scope start

## Purpose

The operator requested the destructive lifecycle apply PRD path first, then a safer non-destructive future-candidate path. This note records the second path without opening implementation or weakening the Phase 18 destructive-apply gate.

## Candidate set from architecture future work

| Candidate | Destructive risk | Notes |
|---|---:|---|
| Event-log prune/apply implementation | High | Covered by Phase 18 PRD gate only; implementation remains blocked. |
| Object-storage lifecycle jobs | Medium/High | Can become destructive or credentialed; should wait until apply safety path is complete. |
| Scheduled jobs | Medium | Automation can trigger operational side effects; needs careful scope. |
| Web dashboard | Low if read-only | Can start as a read-only operator visibility surface with no mutation. |
| GLM adapter | Low/Medium | Runtime adapter work, non-destructive but touches execution behavior and provider config. |
| Split deployment | Medium | Infrastructure/ops change, useful but broader than a safety follow-up. |
| Postgres connection mTLS | Low/Medium | Security hardening; deployment-sensitive but non-destructive. |

## Recommended next non-destructive branch

**Recommended candidate: Read-only web dashboard discovery/planning.**

Rationale:

- It can be constrained to read-only operator visibility and avoid mutation paths.
- It complements lifecycle safety work by making task/session/event state easier to inspect.
- It does not require credentialed production lifecycle operations.
- It can start with PRD/UX/architecture only, preserving the current safety posture.

## Guardrails for the next branch

- Start with BMAD PRD/UX/architecture planning; do not add routes or frontend code until stories are explicit.
- Initial dashboard scope should be read-only: task list, task detail, session status, event/replay status, lifecycle dry-run visibility if already available through existing read surfaces.
- No approve/apply/prune/delete/truncate/move/rewrite/chmod controls in the first dashboard scope.
- No weakening of Tier-3 approval gates, capability boundaries, token scoping, or event-spine append-only assumptions.

## Next workflow recommendation

After Phase 18 PRD-scope commit is green, use `$bmad-create-prd` or `$bmad-create-ux-design` for a **read-only web dashboard** planning slice if the operator wants the non-destructive branch next.
