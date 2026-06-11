---
id: ADR-0025
status: accepted
date: 2026-06-11
supersedes: null
amends: ADR-0024
---

# ADR-0025: Event Log Lifecycle Operations

## Status

**Accepted** — 2026-06-11. Gates Phase 14 (event log lifecycle operations). This ADR authorizes planning, validation, and non-destructive dry-run behavior only. This ADR does not authorize hot-log deletion, truncation, archive mutation, or any other destructive apply path.

## Context

Phase 12 added historical replay. Phase 13 added archive-aware replay support with manifest resolution, checksum validation, hot+archive merge semantics, route-local archive errors, and package-only progress streaming.

The remaining lifecycle question is operational: once archived segments are validated and replayable, operators need a safe way to understand whether hot event-log segments could eventually be pruned. That question is safety-critical because the event spine is the source of truth. A wrong prune can destroy auditability, break replay, and make task/session history unverifiable.

The current system therefore needs a lifecycle operations boundary before any destructive implementation exists.

## Decision

### Decision 1 — Non-destructive dry-run is the only default operation

Phase 14 may introduce lifecycle planning artifacts and dry-run outputs that classify hot event segments as eligible, retained, skipped, or blocked. A dry-run must not delete, truncate, move, rewrite, chmod, or mutate event files or archive manifests.

A valid dry-run can read:

- hot event segment metadata;
- archive manifest entries;
- checksums and byte sizes;
- replay validation results;
- configured retention thresholds.

A valid dry-run can emit a structured plan for operator review. It cannot apply that plan.

### Decision 2 — Destructive prune/apply requires a future explicit operator gate

Any future destructive apply path must be delivered as a separate story and must require all of the following before execution:

1. archive manifest validation passes;
2. replay validation passes against the retained hot+archive set;
3. the dry-run plan is persisted as an immutable, content-addressed artifact or event;
4. the persisted plan includes a stable content hash over the exact segment set, eligibility decisions, blockers, and retention inputs;
5. an explicit Tier-3/operator authorization is recorded in the auditable event spine or an equally durable audit ledger for that exact plan hash;
6. apply execution re-computes and matches the plan hash immediately before mutation, failing closed on any mismatch;
7. rollback/restore evidence is available, including backup/restore instructions for affected hot segments;
8. the command/API name is distinct from dry-run so accidental apply is not possible through a boolean flag typo.

This ADR intentionally does not define the final apply interface. It defines the preconditions a future ADR/story must satisfy.

### Decision 3 — Replay and task-history safety remain higher priority than disk reclamation

If archive validation, replay validation, checksum verification, segment ordering, manifest consistency, or task-history boundaries are ambiguous, lifecycle planning must fail closed. Disk reclamation is never allowed to weaken replay correctness or auditability.

### Decision 4 — `get_task_history` remains hot-log only until separately changed

Archived task-history is out of scope for this slice. `get_task_history` remains hot-log only, matching Phase 13 behavior. If archive-aware task history is added later, it must have separate requirements and tests because it changes an operator-facing query contract.

### Decision 5 — Object storage and scheduled lifecycle jobs are future work

Object-storage lifecycle policies, scheduled prune jobs, cron-like workers, and automatic retention enforcement are out of scope. They can be considered only after the dry-run/apply safety contract is proven.

### Decision 6 — Lifecycle planning must stay separated from replay execution

Future lifecycle planner code may reuse read-only archive manifest and replay validation helpers, but it must not add write, delete, truncate, move, or apply helpers to the replay API surface. If planner code lands inside `packages/replay`, it must be isolated in a lifecycle-specific module with tests proving existing replay and task-history APIs remain read-only. A dedicated lifecycle package/module is preferred if apply semantics are introduced later.

## Consequences

### Positive

- Operators can reason about lifecycle safety before any destructive behavior exists.
- The event spine remains append-only and auditable by default.
- Future prune/apply work has explicit preconditions instead of ad hoc deletion semantics.
- Archived task-history and object-storage automation stay separated from the core safety gate.

### Negative

- Phase 14 does not reclaim disk space by itself.
- Operators must perform an extra review/authorization step before any future destructive apply.
- A future apply story will need additional tests and approval gates beyond this ADR.

## Alternatives considered

- **Implement prune/apply immediately behind a `--dry-run` flag.** Rejected. A boolean flag makes accidental destructive execution too easy before the safety contract is reviewed.
- **Rely on filesystem backups instead of replay validation.** Rejected. Backups mitigate disaster but do not prove the retained hot+archive event set is replayable.
- **Make task history archive-aware in the same slice.** Rejected. It changes a user-facing query contract and should be independently specified.
- **Schedule automatic retention jobs now.** Rejected. Automation should come only after dry-run/apply semantics are proven and operator-gated.

## Linked artifacts

- ADR-0001 — Event spine.
- ADR-0024 — Historical Event Replay.
- `_bmad-output/planning-artifacts/phase-13-prd-amendment.md` — archive-aware replay foundation.
- `_bmad-output/planning-artifacts/phase-14-prd-amendment.md` — Phase 14 requirements.
- `_bmad-output/planning-artifacts/phase-14-architecture-amendment.md` — Phase 14 architecture constraints.
- `docs/operator-runbook.md` — operator lifecycle guidance.
- `docs/backup-restore.md` — backup/restore evidence required before future destructive apply.

— *R2d2, 2026-06-11.*
