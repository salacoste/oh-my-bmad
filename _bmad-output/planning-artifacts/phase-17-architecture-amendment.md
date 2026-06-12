# Phase 17 Architecture Amendment — Destructive Lifecycle Apply Readiness

## Architectural intent

Phase 17 defines the safety architecture for a future destructive event-log lifecycle apply feature. It is a readiness phase: the architecture must make unsafe implementation paths harder by documenting gates and preserving existing read-only/runtime boundaries.

## Existing foundation

- ADR-0025 authorizes planning, validation, and non-destructive dry-run only.
- `packages/replay/src/replay/lifecycle.py` creates immutable dry-run plans and stable `plan_hash` values.
- `packages/replay/src/replay/archive_manifest.py` validates archive manifests, checksums, segment ordering, and hot+archive replay inputs.
- `services/registry-api/src/registry_api/routes/replay.py` exposes replay validation, snapshots, and archive-aware task history but no lifecycle apply route.
- `docs/operator-runbook.md` already documents a future safe sequence: dry-run, replay validation, immutable plan artifact, Tier-3/operator authorization for exact plan hash, re-compute before mutation.

## Phase 17 invariants

1. **No mutation implementation.** Phase 17 must not add delete/truncate/move/rewrite/chmod/prune/apply behavior.
2. **Exact plan identity.** Any later apply design must bind to `LifecycleDryRunPlan.plan_hash` and re-compute the hash immediately before mutation.
3. **Replay-first safety.** Archive manifest validation and replay validation are preconditions to any later apply.
4. **Operator authorization is hash-scoped.** Future Tier-3/operator approval must reference the exact plan hash, affected segment identities, and rollback evidence.
5. **Rollback evidence is required before mutation.** Future apply design must identify backup/restore evidence for every affected hot segment.
6. **Dry-run and apply are distinct surfaces.** A future apply command/API cannot be enabled by flipping a `dry_run=false` boolean.
7. **Object storage and scheduling remain future.** S3/B2/R2 lifecycle policies and scheduled retention workers are still out of scope until apply semantics are proven.

## Allowed write set

Phase 17 may edit only BMAD planning/status/docs and optional static verification artifacts. Runtime/package/API/deployment source changes are out of scope for Phase 17; any future non-destructive guard or runtime-source change requires a separate explicitly planned story/phase and must still not expose or execute apply behavior.

## Forbidden write set

- `Path.unlink`, `os.remove`, `shutil.rmtree`, `.truncate`, `.replace`, `.rename`, `.chmod` in lifecycle apply context.
- New replay public exports containing `apply`, `prune`, `delete`, `truncate`, `rewrite`, `move`, or `chmod`.
- New registry-api route for lifecycle apply/prune.
- New console/Telegram/MCP tool for lifecycle apply/prune.
- New scheduled retention worker or object-storage lifecycle policy.
- New credentialed production operation.

## Verification strategy

Phase 17 verification should include:

- YAML/status parse and Phase 17 status scan.
- Grep/static guard for destructive route/tool/export/job names.
- Existing lifecycle/replay tests to prove dry-run and archive-aware history behavior remains intact.
- `ruff`, `mypy` if Python files change; otherwise at least `git diff --check` and docs/static checks.
- Independent code-reviewer and architect review before final checkpoint.
