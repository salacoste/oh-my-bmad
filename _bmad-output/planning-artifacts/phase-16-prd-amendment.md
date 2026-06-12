# Phase 16 PRD Amendment — Archive-Aware Task History (P16-AATH)

## Goal

Extend the existing read-only task-history endpoint so operators can retrieve history for tasks whose events live only in validated archive segments, while preserving the Phase 14/15 lifecycle safety boundary.

## Scope

- IN: archive-aware task-history requirements, opt-in archive manifest behavior, route-level failure semantics, focused package/API tests, and docs/status reconciliation.
- OUT: destructive lifecycle apply, hot-log deletion/truncation/move/rewrite/chmod, object-storage lifecycle policies, scheduled retention workers, snapshot behavior changes, replay/validate response-shape changes, and credentialed production operations.

## Functional Requirements

- FR152: `GET /v1/tasks/{task_id}/history` can include events from validated archive segments when archive manifest configuration is present.
- FR153: With no archive manifest configured, task history remains hot-log-only and preserves current response shape, pagination, ordering, and 404 semantics.
- FR154: Invalid archive configuration or manifest data fails closed for task history using the same route-local ProblemDetails error family used by replay/validate.
- FR155: Archive-aware task history remains read-only and does not mutate hot logs, archive files, live database state, snapshots, or lifecycle plans.

## Acceptance Criteria

- A valid archive manifest containing an archive-only task makes `/v1/tasks/{task_id}/history` return that task's history.
- Existing hot-log task-history behavior is unchanged when no archive manifest env/config is set.
- Hot and archive events are returned in deterministic monotonic order and paginated after merge/filter.
- Invalid archive manifest configuration returns route-local RFC 7807 ProblemDetails rather than an unhandled internal error.
- Snapshot creation remains hot-only and lifecycle dry-run remains non-destructive.
- Docs state that archive-aware task history is read-only and does not authorize destructive lifecycle apply.

## Non-Functional Requirements

- NFR-O22: Archive-aware history uses existing archive manifest validation and deterministic envelope collection; no second archive parser is introduced.
- NFR-M13: Response shape stays additive-compatible with Phase 12 task-history clients.
- NFR-S18: No new write path, subprocess spawn site, credential surface, or broad token propagation is introduced.
- NFR-R18: Invalid archive configuration fails closed before returning partial archive-derived history.

## Verification Expectations

- Route tests for archive-only task history, default hot-only behavior, ordering/pagination, and invalid archive config.
- Package/API targeted pytest.
- `ruff`, relevant `mypy`, `check_imports.py`, `check_single_writer.py`, and `git diff --check`.
- Final ai-slop-cleaner and independent code-reviewer + architect review before commit/push.
