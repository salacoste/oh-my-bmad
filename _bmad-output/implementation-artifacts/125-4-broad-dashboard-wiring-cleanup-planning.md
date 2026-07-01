# Story 125.4 — Broad Dashboard Wiring Cleanup Planning

Status: done after sequential Architect APPROVE/CLEAR followed by Critic APPROVE/CLEAR consensus
Scope: docs/status-only
Context snapshot: `.omx/context/next-likely-work-open-phase-46-epic-125-planning-20260630T213657Z.md`
Deep-interview handoff: `.omx/interviews/phase-46-epic-125-dashboard-task-list-expansion-planning-deep-interview-complete.md`
Ralplan plan: `.omx/plans/story-125-phase-46-dashboard-task-list-expansion-planning-plan.md`
Test spec: `.omx/specs/story-125-phase-46-dashboard-task-list-expansion-planning-test-spec.md`
Canonical architecture source: `../planning-artifacts/phase-46-architecture-amendment.md`

## Decision

Decision classification: `high-risk-deferred`.

Broad dashboard wiring cleanup is not selected for runtime in Phase 46 and must begin later with inventory/test guards only.

## Planning constraints

- Inventory modules/contracts before any cleanup.
- Split future cleanup into narrow behavior-preserving stories.
- No broad rewiring, behavior changes, generated live data, service/dependency/CI changes, or production operations.

## Non-authorization statement

This story is docs/status-only. It does not add runtime implementation, backend/API behavior changes, test-code changes, browser network calls, dashboard JavaScript/HTML behavior changes, dependencies, lockfiles, CI/deployment changes, services, MCP changes, generated live data, hidden selectors, automatic traversal, row-derived selectors, URL/hash/local-storage/session-storage/cookie state, replay execution target selection, lifecycle apply/prune/rollback, mutation/control behavior, broad dashboard wiring implementation, production credentials, or production operations.

## Future test obligations

A future implementation story, if separately approved, must include failing-first tests for the exact selected boundary, fail-closed malformed/hidden/adjacent selector cases, preservation of all existing aggregate-task-list API/browser contracts, and no unauthorized side channels.

## Planning artifacts

- `_bmad-output/planning-artifacts/phase-46-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-46-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-46-epics.md`
- `.omx/plans/story-125-phase-46-dashboard-task-list-expansion-planning-plan.md`
- `.omx/specs/story-125-phase-46-dashboard-task-list-expansion-planning-test-spec.md`

## Consensus evidence

- Architect review cycle 0: request_changes / WATCH for status-evidence coherence; persisted at `.omx/artifacts/ralplan/story-125-architect-review-cycle-0.md`.
- Architect final review: approve / CLEAR; persisted at `.omx/artifacts/ralplan/story-125-architect-review.md`.
- Critic review: approve / CLEAR; persisted at `.omx/artifacts/ralplan/story-125-critic-review.md`.

## Verification plan

- Verify this story remains docs/status-only.
- Verify non-authorization boundaries are explicit.
- Verify sprint-status and feature-status summarize this story without claiming runtime implementation.
- Run YAML parse and `git diff --check`.

## Completion evidence

Story completes as docs/status-only planning after sequential Architect final APPROVE/CLEAR followed by Critic APPROVE/CLEAR. Runtime/source/test/backend/API/dashboard JavaScript/HTML/dependency/CI changes remain unstarted and unauthorized.

## Completion timestamp

2026-06-30T21:44:33Z
