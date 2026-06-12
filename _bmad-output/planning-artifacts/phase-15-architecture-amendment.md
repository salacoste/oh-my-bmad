# Phase 15 Architecture Amendment — Lifecycle Documentation Reconciliation and Backlog Triage

## Decision Summary
Phase 15 is documentation/status reconciliation only. It updates the project’s canonical docs to reflect Phase 14 lifecycle-operation boundaries and triages future candidates without changing runtime, API, storage, or deployment behavior.

## Architectural Invariants
1. **Docs reflect the shipped boundary.** Replay can consume validated archives; lifecycle operations remain planning/validation/dry-run only.
2. **No implementation authority.** This phase does not authorize archive-aware task-history, destructive apply, object storage, or scheduled jobs.
3. **ADR-0025 remains the safety gate.** Any future destructive apply must bind operator authorization to an exact dry-run plan hash and fail closed if recomputation differs.
4. **Hot task history remains default.** `GET /v1/tasks/{task_id}/history` remains hot-log-only until separately specified and tested.

## Allowed tracked write set
- `_bmad-output/planning-artifacts/phase-15-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-15-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-15-epics.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/retrospectives/phase-15-retrospective.md`
- `docs/api-contracts.md`
- `docs/operator-runbook.md`
- `docs/data-models.md`
- `docs/architecture.md`
- `docs/project-overview.md`
- `docs/index.md`

## Forbidden tracked write set
- `services/`
- `packages/`
- `mcp-servers/`
- `scripts/`
- `pyproject.toml`
- `uv.lock`
- `docker-compose.yml`
- `docker-compose.macos.yml`

## Verification Strategy
- YAML parse/status scan for sprint-status values.
- Diff allowlist check against the docs/planning/status set above.
- Forbidden-path diff check for runtime/API implementation and dependency/deployment files.
- Grep checks for Phase 15 complete docs, Phase 14 lifecycle boundary, hot-log-only task history, ADR-0025, and future-candidate wording.
- `git diff --check` hygiene.
