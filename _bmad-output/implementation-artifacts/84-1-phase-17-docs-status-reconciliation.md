# Story 84.1: Phase 17 docs/status reconciliation and no-runtime proof

## Status

Done.

## Story

As a maintainer,
I want Phase 16 closure and Phase 17 readiness scope reflected in repository docs/status without runtime changes,
so that future lifecycle apply work starts from a reviewed safety contract and does not accidentally ship destructive behavior.

## Acceptance criteria

1. README and docs identify Phase 17 as open and Phase 16 as shipped/closed.
2. API, operator, data-model, and architecture docs state that destructive lifecycle apply remains unimplemented.
3. Phase 17 scope is planning/readiness only and references exact `plan_hash`, replay validation, rollback evidence, and operator authorization preconditions for future apply.
4. Changed tracked files are limited to docs and BMAD planning/status/implementation artifacts.
5. Static scans find no new runtime/package/API/deployment behavior or destructive lifecycle implementation surface.

## No-runtime behavior proof

Tracked changed paths are limited to:

- `README.md`
- `docs/api-contracts.md`
- `docs/architecture.md`
- `docs/data-models.md`
- `docs/index.md`
- `docs/operator-runbook.md`
- `docs/project-overview.md`
- `_bmad-output/planning-artifacts/phase-17-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-17-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-17-epics.md`
- `_bmad-output/implementation-artifacts/81-1-phase-17-planning.md`
- `_bmad-output/implementation-artifacts/84-1-phase-17-docs-status-reconciliation.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

No files under `packages/`, `services/`, `mcp-servers/`, `scripts/`, deployment manifests, lockfiles, or CI workflows were modified for this story.

## Static destructive-surface guard

Phase 17 did not add:

- lifecycle apply/prune route, CLI, MCP, worker, cron, or scheduler surface;
- delete/truncate/move/rewrite/chmod implementation;
- archive mutation or object-storage lifecycle job;
- credentialed production operation;
- weakening of ADR-0025, `HOT_ONLY_REPLAY`, archive checksum validation, or route-local archive errors.
