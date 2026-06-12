# Stories 82.1 and 83.1: Phase 17 contract status and no-runtime proof

## Status

Done.

## Summary

Phase 17 now has explicit future destructive lifecycle apply readiness contracts for:

- exact dry-run `plan_hash` authorization evidence (Story 82.1);
- replay-validation proof and rollback-evidence preconditions (Story 83.1).

These contracts remain docs/status-only. They do not implement destructive lifecycle apply.

## Changed contract artifacts

- `_bmad-output/implementation-artifacts/82-1-plan-hash-authorization-contract.md`
- `_bmad-output/implementation-artifacts/83-1-replay-validation-rollback-evidence-contract.md`
- `_bmad-output/planning-artifacts/phase-17-prd-amendment.md`
- `_bmad-output/planning-artifacts/phase-17-architecture-amendment.md`
- `_bmad-output/planning-artifacts/phase-17-epics.md`
- `docs/operator-runbook.md`
- `docs/api-contracts.md`
- `docs/data-models.md`

## No-runtime proof

The change set is limited to docs and BMAD artifacts. It must not include changes under:

- `packages/`
- `services/`
- `mcp-servers/`
- `scripts/`
- `.github/`
- deployment manifests
- lockfiles
- dependency declarations

Machine-check anchors: no runtime, no package, no deployment behavior is changed.

## Destructive-surface proof

No apply/prune command, route, MCP tool, worker, scheduler, object-storage lifecycle job, archive mutation, delete/truncate/move/rewrite/chmod helper, credentialed production operation, dependency, or CI change is introduced.

## Verification commands

- Sprint-status YAML parse/status assertions.
- Changed-path allowlist check.
- Stale/destructive source path scan.
- `git diff --check`.
