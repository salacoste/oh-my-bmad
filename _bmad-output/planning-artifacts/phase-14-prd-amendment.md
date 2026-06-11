# Phase 14 PRD Amendment — Event Log Lifecycle Operations (P14-ELLO)

## Goal
Turn the Phase 13 event-log lifecycle foundation into an operator-safe lifecycle operations plan without introducing destructive behavior by default.

## Scope
- IN: sprint-status hygiene, ADR/operator gate for prune/apply semantics, non-destructive lifecycle dry-run contract, archived task-history boundary decision, operator documentation, verification plan, and retrospective closure.
- OUT: actual hot-log deletion, archive object-store lifecycle jobs, scheduled prune workers, public replay streaming endpoint, credentialed production operations, lossy compaction, and archive-aware task-history retrieval.

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
- Any code shipped in Phase 14 is non-destructive by construction and covered by targeted tests.
- Epic 73 closure publishes final status, documentation, and retrospective evidence without runtime/API changes.

## Linked Design Gate
- `docs/adr/0025-event-log-lifecycle-operations.md` — accepted ADR for non-destructive lifecycle operations and future operator-gated prune/apply preconditions.

## Current Autopilot Slice Boundary (2026-06-11, Epic 73)

This Autopilot run chooses the **verification, docs, and retrospective closure**
slice. It does not reopen runtime, route, API, task-history, or lifecycle planner
implementation scope.

### Exact deliverables for this run

- `_bmad-output/planning-artifacts/phase-14-prd-amendment.md`: current Epic 73 closure boundary and completion evidence.
- `_bmad-output/planning-artifacts/phase-14-architecture-amendment.md`: closure-only architecture handoff and forbidden-path guard.
- `_bmad-output/planning-artifacts/phase-14-epics.md`: Epic 69-73 completion map.
- `_bmad-output/implementation-artifacts/sprint-status.yaml`: Phase 14 completion rows and audit event.
- `_bmad-output/retrospectives/phase-14-retrospective.md`: shipped scope, verification, lessons, and carry-forward.
- `docs/project-overview.md` and `docs/index.md`: Phase 14 complete project summary.

### Explicitly deferred from this run

- No archive-aware task-history retrieval.
- No new task-history query parameter or response shape.
- No CLI command for prune, apply, lifecycle, delete, truncate, move, or rewrite.
- No destructive apply/delete/truncate/move/rewrite/chmod implementation.
- No lifecycle dry-run planner semantic change.
- No API-contract, operator-runbook, service, package, MCP, script, dependency, or deployment edits.

## Historical completed slice — Epic 72

Epic 72 previously completed the task-history archive-boundary contract lock by
preserving hot-log-only `get_task_history`, adding route-level regression coverage
for archive-manifest isolation, and documenting archive-aware task history as a
future story. That Epic 72 boundary is completed historical context only and is
not operative authorization for this Epic 73 closure run.

## Phase 14 completion evidence target

Epic 73 closes Phase 14 when:

1. Phase 14 planning artifacts clearly identify Epic 73 as the active closure slice.
2. Sprint status marks Epics 69-73 complete and records `phase-14-complete`.
3. Project overview and documentation index state Phase 14 complete.
4. The retrospective exists under `_bmad-output/retrospectives/phase-14-retrospective.md`.
5. Verification proves the final diff stayed inside the Epic 73 closure allowlist and touched no forbidden runtime/API paths.
