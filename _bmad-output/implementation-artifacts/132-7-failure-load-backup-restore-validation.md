# Story 132.7 — Failure/load/backup/restore validation readiness

## Summary

Story 132.7 adds readiness-only validation evidence for future failure drills,
bounded load validation, and backup/restore validation. The canonical contract is
`docs/failure-load-backup-restore-readiness.json` and the checker is:

```bash
uv run python scripts/check_failure_load_backup_restore_readiness.py
uv run python scripts/check_failure_load_backup_restore_readiness.py --self-test
```

This story does not execute live drills, generate load, run destructive restores,
prune backups, mutate production hosts, provision infrastructure, activate
production, add credential values, or make runtime audit emitters live. Local
single-host SQLite defaults remain preserved.

## Readiness scope

The static contract records required future validation evidence for:

1. Failure scenarios: database outage, network partition, pool exhaustion, worker
   crash, orchestrator crash, registry restart, MCP service unavailable,
   event-log append failure, migration failure/rollback, and backup restore
   failure.
2. Load validation: explicit target surfaces, bounded synthetic load only,
   latency/error/backpressure metrics, pool saturation thresholds, rate-limit
   preservation, trace correlation, and no external production load.
3. Backup/restore validation: pre-migration backup, checksum/manifest
   validation, isolated restore, schema/version compatibility, point-in-time
   freshness, rollback/fix-forward decision, and a future destructive restore
   confirmation boundary.
4. Observability/audit: health/readiness signals, sanitized logs, trace IDs,
   recovery timeline, audit metadata only, and no secret material.
5. Fail-closed boundaries: no live destructive operation, backup pruning,
   production restore, host mutation, credential values, or runtime audit
   emitter.

## Verification commands

Focused verification for this story:

```bash
uv run python scripts/check_failure_load_backup_restore_readiness.py --self-test
uv run python scripts/check_failure_load_backup_restore_readiness.py --verbose
uv run pytest -q tests/scripts/test_check_failure_load_backup_restore_readiness.py
uv run ruff check scripts/check_failure_load_backup_restore_readiness.py tests/scripts/test_check_failure_load_backup_restore_readiness.py
uv run ruff format --check scripts/check_failure_load_backup_restore_readiness.py tests/scripts/test_check_failure_load_backup_restore_readiness.py
```

## Status

Story 132.7 is complete as a readiness-only validation gate. Current status note:
Epic 132 and Story 132.8 are now `readiness-contract-complete_not_live_activation` /
done as of Story 132.8 closure evidence. This artifact remains readiness-only and
does not imply live activation.
