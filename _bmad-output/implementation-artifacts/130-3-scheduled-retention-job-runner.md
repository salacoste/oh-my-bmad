# Story 130.3 — Scheduled retention job runner

Status: done locally on 2026-07-06.

Story 130.3 adds `packages/replay/src/replay/retention_runner.py`, a package-local
metadata-only runner around the Story 130.2 `create_retention_dry_run_plan(...)`
planner. The runner is default-disabled unless explicitly enabled, accepts only an
externally supplied schedule slot, and does not add cron, systemd timers, daemon
loops, live scheduler activation, storage calls, credential loading, object delete
or transition, archive/manifest mutation, backup pruning, command surfaces,
registry mutation endpoints, deployment behavior, or runtime audit emitters.

## Implemented contract

- `RetentionRunnerConfig(enabled=False)` keeps the runner disabled by default.
- `max_concurrency exactly 1` is enforced by configuration validation.
- `run_scheduled_retention_job(...)` uses a single lock-protected metadata ledger.
- A deterministic pre-run idempotency key is derived from schedule slot, runner
  policy/version/fingerprint, planner identity/version, mode,
  `pre_run_input_reference`, `policy_input_reference`, and
  `manifest_input_reference`.
- Idempotency excludes trace id, retry metadata, planner `generated_at`, plan hash,
  policy path, manifest path, policy id/version, manifest id/generated-at,
  artifact filename, and action counts.
- Status vocabulary is `disabled`, `lock_contended`, `started`, `retrying`,
  `completed`, `terminal_failure`, and `apply_deferred`.
- `started` is transient/internal and transitions within the same invocation.
- Completed replay returns persisted metadata and does not call the planner within
  the injected ledger/status store; the default in-memory ledger is process-local.
- Dry-run mode calls `create_retention_dry_run_plan(...)` and records post-run
  evidence: plan hash, artifact filename, policy id/version, manifest id/generated
  time, action counts, and blocker count.
- Apply mode returns `apply_deferred` metadata only.
- Retry/backoff evidence is fakeable metadata; no real sleep is performed.

## Verification targets

- `packages/replay/src/replay/test_retention_runner.py`
- `tests/scripts/test_check_retention_policy_readiness.py`
- `scripts/check_retention_policy_readiness.py --self-test`
- `scripts/check_retention_policy_readiness.py --verbose`

## Non-goals preserved

Story 130.3 is metadata-only. Object-storage deletion/transition, external storage
calls, production credential loading, manifest mutation, backup pruning, live
scheduler activation, dashboard/API mutation surfaces, registry routes, deployment
changes, and runtime audit emitters remain deferred/fail-closed for later stories.
