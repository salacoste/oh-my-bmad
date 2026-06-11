# Phase 14 PRD Amendment — Event Log Lifecycle Operations (P14-ELLO)

## Goal
Turn the Phase 13 event-log lifecycle foundation into an operator-safe lifecycle operations plan without introducing destructive behavior by default.

## Scope
- IN: sprint-status hygiene, ADR/operator gate for prune/apply semantics, non-destructive lifecycle dry-run contract, archived task-history boundary decision, docs/runbook updates, verification plan.
- OUT: actual hot-log deletion, archive object-store lifecycle jobs, scheduled prune workers, public replay streaming endpoint, credentialed production operations, lossy compaction.

## Functional Requirements
- FR144: Sprint tracking remains machine-parseable after Phase 13; legacy non-standard statuses are normalized or explicitly moved to metadata/comments.
- FR145: A Phase 14 ADR defines safe event-log prune/apply semantics, required operator authorization, rollback evidence, and explicit non-goals.
- FR146: Lifecycle dry-run output is non-destructive and sufficient for an operator to understand which hot segments would be eligible, retained, or blocked.
- FR147: Archived task-history behavior is explicitly bounded: hot-only remains default until a future story implements archive-aware history with separate acceptance tests.
- FR148: Operator docs explain the safe sequence: validate archives, inspect dry-run, obtain Tier-3/operator gate, then run a future destructive apply path only after separate authorization.

## Acceptance Criteria
- BMad sprint status contains no invalid story status values under `development_status`.
- Planning artifacts identify destructive operations as out-of-scope unless a separate operator gate is approved.
- ADR records the invariant that replay/archive validation must pass before any future prune/apply can be considered.
- Any code shipped in this slice is non-destructive by construction and covered by targeted tests.
- Because this slice adds an operator-facing route contract regression, code review and QA must use fresh registry-api replay-route verification evidence rather than a docs-only skip.


## Linked Design Gate
- `docs/adr/0025-event-log-lifecycle-operations.md` — accepted ADR for non-destructive lifecycle operations and future operator-gated prune/apply preconditions.

## Autopilot Slice Boundary (2026-06-11, Epic 72)
This Autopilot run chooses the **task-history archive-boundary contract-lock slice**.
It implements FR147 by preserving the existing hot-log-only `get_task_history`
behavior, adding route-level regression coverage for archive-manifest isolation,
and documenting archive-aware task history as a separate future story.

### Exact Deliverables for This Run
- `_bmad-output/planning-artifacts/phase-14-prd-amendment.md`: Epic 72 slice
  boundary and verification language.
- `_bmad-output/planning-artifacts/phase-14-architecture-amendment.md`: route-level
  contract-test allowance for Epic 72 while preserving no destructive behavior.
- `_bmad-output/planning-artifacts/phase-14-epics.md`: Epic 72 active/completed
  handoff and Epic 73 future status.
- `services/registry-api/src/registry_api/routes/test_replay.py`: regression proving
  archive manifests do not make `/v1/tasks/{task_id}/history` read archived-only
  task events.
- `services/registry-api/src/registry_api/routes/replay.py`: optional docstring
  clarification only; no archive-aware task-history implementation.
- `docs/api-contracts.md` and `docs/operator-runbook.md`: explicit separation of
  replay/validate archive env vars from hot-only task-history source selection.

### Explicitly Deferred From This Run
- No archive-aware task-history retrieval.
- No new task-history query parameter or response shape.
- No CLI command for prune, apply, lifecycle, delete, truncate, move, or rewrite.
- No destructive apply/delete/truncate/move/rewrite/chmod implementation.
- No lifecycle dry-run planner semantic change.
