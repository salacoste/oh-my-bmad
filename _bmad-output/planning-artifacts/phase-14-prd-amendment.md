# Phase 14 PRD Amendment — Event Log Lifecycle Operations (P14-ELLO)

## Goal
Turn the Phase 13 event-log lifecycle foundation into an operator-safe lifecycle operations plan without introducing destructive behavior by default.

## Scope
- IN: sprint-status hygiene, ADR/operator gate for prune/apply semantics, non-destructive lifecycle dry-run contract, archived task-history boundary decision, docs/runbook updates, verification plan.
- OUT: actual hot-log deletion, archive object-store lifecycle jobs, scheduled prune workers, public replay streaming endpoint, credentialed production operations, lossy compaction.

## Functional Requirements
- FR144: Sprint tracking remains machine-parseable after Phase 13; legacy non-standard statuses are normalized or explicitly moved to metadata/comments.
- FR145: A Phase 14 ADR defines safe event-log prune/apply semantics, required operator authorization, rollback evidence, and explicit non-goals.
- FR146: Lifecycle dry-run output is non-destructive and sufficient for an operator to understand which hot segments would be eligible, retained, skipped, or blocked.
- FR147: Archived task-history behavior is explicitly bounded: hot-only remains default until a future story implements archive-aware history with separate acceptance tests.
- FR148: Operator docs explain the safe sequence: validate archives, inspect dry-run, obtain Tier-3/operator gate, then run a future destructive apply path only after separate authorization.

## Acceptance Criteria
- BMad sprint status contains no invalid story status values under `development_status`.
- Planning artifacts identify destructive operations as out-of-scope unless a separate operator gate is approved.
- ADR records the invariant that replay/archive validation must pass before any future prune/apply can be considered.
- Any code shipped in this slice is non-destructive by construction and covered by targeted tests.
- If this slice remains docs/status-only, UltraQA is explicitly skipped with evidence that no runtime behavior changed.


## Linked Design Gate
- `docs/adr/0025-event-log-lifecycle-operations.md` — accepted ADR for non-destructive lifecycle operations and future operator-gated prune/apply preconditions.

## Autopilot Slice Boundary (2026-06-11)
This Autopilot run chooses the **docs/status-only safety slice**. It does not implement FR146 as executable planner code. FR146 is captured as the future dry-run contract that a later story must implement after this ADR boundary is accepted.

### Exact Deliverables for This Run
- `docs/adr/0025-event-log-lifecycle-operations.md`
- `_bmad-output/planning-artifacts/phase-14-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-14-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-14-epics.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml` hygiene only: normalize the legacy `bootstrap-minimum-milestone` status to a valid BMad value while preserving rationale in the comment.
- `docs/operator-runbook.md` Phase 14 lifecycle operations boundary: validate archives/replay, inspect content-addressed dry-run plan hash, require durable operator authorization, and defer future apply to a separate approved story.

### Explicitly Deferred From This Run
- No `packages/replay` dry-run planner module.
- No `registry-api` endpoint or API-contract change.
- No fulfillment of executable FR146 dry-run planner; only the operator-doc/runbook contract for a future content-addressed dry-run plan is recorded.
- No CLI command for prune, apply, lifecycle, delete, truncate, move, or rewrite.
- No archived task-history behavior change.
