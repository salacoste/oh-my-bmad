# Phase 16 Architecture Amendment — Archive-Aware Task History

## Decision Summary

Phase 16 activates the first future candidate surfaced by Phase 15: archive-aware task-history retrieval. The implementation must remain a read-only query extension over the existing archive manifest and replay envelope collection contracts.

## Architectural Invariants

1. **Read-only query extension.** Task history may read validated archive segments but must not mutate hot logs, archive segments, manifests, snapshots, lifecycle plans, or live database state.
2. **Existing archive validation is reused.** The route must use the Phase 13 archive manifest path resolution and `collect_replay_envelopes` semantics instead of introducing a second archive parser.
3. **Hot-log default remains stable.** Without archive manifest configuration, `/v1/tasks/{task_id}/history` behaves as it did in Phase 12-15.
4. **Failure is fail-closed.** Archive config, checksum, missing-segment, manifest, and conflict errors map to route-local ProblemDetails and do not return partial archive history.
5. **ADR-0025 remains the destructive-operation gate.** This phase does not authorize lifecycle apply/delete/truncate/move/rewrite/chmod or object-storage/scheduled retention.

## Expected Design

- Move task-history event collection from hot-only glob scanning to a helper that accepts `archive_manifest_path` and delegates to `replay.archive_manifest.collect_replay_envelopes`.
- Filter merged envelopes by payload `task_id` and reuse the existing `TaskHistoryEntry`/`TaskHistoryResponse` shape.
- Keep pagination after deterministic sequence ordering.
- Reuse `_archive_manifest_path()` and `_archive_problem_response()` in `registry_api.routes.replay` for history just as replay/validate already do.

## Allowed tracked write set

- `_bmad-output/planning-artifacts/phase-16-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-16-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-16-epics.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/76-1-phase-16-planning.md`
- `packages/replay/src/replay/` read-only helper/tests if needed
- `services/registry-api/src/registry_api/routes/replay.py`
- `services/registry-api/src/registry_api/routes/test_replay.py`
- `docs/api-contracts.md`
- `docs/operator-runbook.md`
- `docs/data-models.md`
- `docs/architecture.md`
- `docs/index.md`

## Forbidden tracked write set

- Destructive lifecycle apply/prune command surfaces
- Object-storage lifecycle jobs or scheduled retention workers
- Snapshot route behavior changes
- Credential/deployment dependency changes unless a blocker proves they are required
- Any code path that deletes, truncates, moves, rewrites, chmods, or mutates hot/archive logs

## Verification Strategy

- Route tests prove archive-only history retrieval and fail-closed invalid archive config.
- Regression tests prove no manifest configured keeps current hot-log behavior.
- Static checks prove no new spawn or single-writer violation.
- Docs grep proves destructive lifecycle work remains future/non-goal.
