# Phase 17 PRD Amendment — Destructive Lifecycle Apply Readiness (P17-DLAR)

## Summary

Phase 17 opens the next safe BMAD slice after Phase 16: **readiness and planning for a future destructive event-log lifecycle apply feature**. It does not implement apply/prune. It turns the ADR-0025 future-apply preconditions into explicit requirements, acceptance criteria, and forbidden paths so a later implementation phase cannot smuggle destructive behavior behind a dry-run flag.

## Problem

Phase 14 shipped a non-destructive dry-run planner and ADR-0025. Phase 16 shipped archive-aware task history. Operators still need a future way to reclaim hot event-log disk space, but destructive apply is high-risk because the event spine is the audit source of truth. Before code exists, the product contract must define exact safety gates.

## Scope

IN:

- Planning/status artifacts for destructive lifecycle apply readiness.
- Requirements for plan-hash binding, replay validation, rollback evidence, and explicit operator authorization.
- Documentation that Phase 17 is planning/readiness only.
- Static/doc verification that no destructive runtime behavior was introduced.

OUT:

- Any implementation that deletes, truncates, moves, rewrites, chmods, prunes, mutates archives, mutates manifests, or mutates hot logs.
- Apply/prune HTTP, CLI, MCP, worker, cron, scheduled job, or object-storage lifecycle surface.
- Credentialed production operations.
- Snapshot behavior changes.
- Weakening `HOT_ONLY_REPLAY`, archive checksum validation, route-local archive ProblemDetails, or append-only event-spine assumptions.

## Functional requirements

- **FR156 — Phase 17 readiness scope.** The repository records Phase 17 as a BMAD planning/readiness phase for future destructive lifecycle apply, not an implementation phase.
- **FR157 — Exact dry-run plan-hash binding.** Future apply requirements bind operator authorization to the exact content-addressed dry-run plan hash and require re-computation immediately before mutation.
- **FR158 — Replay validation precondition.** Future apply requirements require archive manifest validation and replay validation against the retained hot+archive set before any mutation.
- **FR159 — Rollback/restore evidence.** Future apply requirements require backup/restore evidence for affected hot segments before mutation.
- **FR160 — Distinct apply surface.** Future apply requirements prohibit a boolean dry-run/apply toggle; any later apply command/API must be clearly distinct from dry-run.
- **FR161 — No destructive behavior in Phase 17.** Phase 17 must not change runtime/package/API/deployment behavior except safe static/doc verification if explicitly justified.

## Non-functional requirements

- **NFR-S24 — Fail-closed lifecycle safety.** Ambiguous archive coverage, replay validation, plan hash, operator authorization, or rollback evidence blocks future apply.
- **NFR-O22 — Auditable authorization.** Future apply authorization evidence must be durable and tied to the exact plan hash.
- **NFR-M18 — Dry-run/apply separability.** Planning docs and any future implementation must keep dry-run generation separate from mutation execution.

## Acceptance criteria

1. Phase 17 planning artifacts define requirements, architecture constraints, epics/stories, non-goals, and forbidden paths.
2. Sprint status shows Phase 17 open with planning/readiness story complete and later stories backlog.
3. Docs/status no longer present Phase 16 as open after its shipped commit.
4. Verification proves no destructive lifecycle apply route, public replay export, CLI/MCP tool, worker, scheduled retention, object-storage lifecycle job, or filesystem mutation helper was introduced.
5. Final review confirms ADR-0025 remains the destructive-operation gate.
