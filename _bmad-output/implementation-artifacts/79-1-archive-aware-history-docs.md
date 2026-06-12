# Story 79.1 — Archive-aware history docs

Status: done

## Summary

Updated operator/API/data/architecture docs to reflect Phase 16 archive-aware task-history behavior while preserving ADR-0025 destructive-operation boundaries.

## Updated docs

- `docs/api-contracts.md`
- `docs/operator-runbook.md`
- `docs/data-models.md`
- `docs/architecture.md`
- `docs/project-overview.md`
- `docs/index.md`
- `README.md`

## Safety wording preserved

- Destructive lifecycle apply/delete/truncate/move/rewrite/chmod remains future work.
- Object-storage lifecycle jobs and scheduled retention remain future work.
- Snapshot creation remains hot-log-only via `HOT_ONLY_REPLAY`.
- Task history remains a read-only query even when archive-aware.
