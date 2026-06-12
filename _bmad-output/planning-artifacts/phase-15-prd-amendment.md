# Phase 15 PRD Amendment — Lifecycle Documentation Reconciliation and Backlog Triage (P15-LDRBT)

## Goal
Close the documentation consistency gap intentionally left after Phase 14 by reconciling deeper lifecycle docs and turning Phase 14 carry-forward items into explicit future candidates without changing runtime behavior.

## Scope
- IN: API contract wording, operator runbook wording, data-model lifecycle contract, architecture phase map/future-work list, sprint-status tracking, and future-candidate triage.
- OUT: runtime/API implementation, task-history archive retrieval, destructive lifecycle apply, object-storage lifecycle jobs, scheduled retention workers, dependency changes, deployment changes, and credentialed production operations.

## Functional Requirements
- FR149: API, data-model, architecture, and operator docs describe Phase 12-14 lifecycle contracts consistently.
- FR150: Phase 14 carry-forward items are triaged into explicit future candidates, not hidden in retrospective prose only.
- FR151: Sprint status tracks the docs/backlog reconciliation slice with valid BMad statuses.

## Acceptance Criteria
- Docs no longer describe replay/lifecycle as only “Phase 12/13” where Phase 14 boundaries are relevant.
- Architecture has a Phase 14 lifecycle-operations section and a “Future work beyond Phase 15” list.
- API/data/operator docs preserve the hot-log-only task-history boundary and ADR-0025 destructive-apply gate.
- No runtime/API/service/package/MCP/script/dependency/deployment files change.
- Verification proves YAML/status validity, docs references, forbidden-path diff absence, and whitespace hygiene.

## Future candidates surfaced by this slice
1. Archive-aware task-history retrieval with a separate API/story/test contract.
2. Destructive lifecycle apply planning with ADR/operator gate, exact plan-hash authorization, replay validation, and rollback evidence.
3. Object-storage lifecycle policies and scheduled retention workers after dry-run/apply safety is proven.
4. Optional follow-up docs refresh whenever a future candidate becomes active.
