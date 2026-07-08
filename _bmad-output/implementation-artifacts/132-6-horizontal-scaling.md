# Story 132.6 — Horizontal scaling readiness

## Summary

Story 132.6 adds a static horizontal-scaling readiness contract in
`docs/horizontal-scaling-readiness.json` and enforces it with
`scripts/check_horizontal_scaling_readiness.py` plus focused pytest coverage.
This is readiness-only evidence: local single-host SQLite defaults remain
unchanged and no live scaling, external load balancer, external worker pool,
provisioning, production credential, production host mutation, live load, live
restore, or runtime production audit emitter is activated.

## What changed

- Added a scale-safety matrix for `registry-api`, `registry-state`,
  `telegram-gateway`, `orchestrator-adapter`, `worker-wrapper`, and
  `clawhip-daemon` with explicit classes, reasons, and limit notes.
- Recorded singleton authorities for mutable registry-state writer/materializer,
  Alembic migration runner, retention/apply/destructive lifecycle runner, event
  append authority, and clawhip bridge authority.
- Required coordination boundaries for shared idempotency storage, worktree lock
  ownership, task/session registry consistency, event ordering/replay,
  capability tier preservation, and bounded DB pool composition.
- Added load-balancer readiness criteria for health/readiness endpoints, trace
  propagation, auth header preservation, rate-limit behavior, sticky-session
  stance, no host ports, and no external load balancer.
- Explicitly marked unsupported modes: external worker pool, multi-writer
  registry-state, multi-runner migration, multi-clawhip appenders, dashboard
  live scaling, and runtime audit emitter activation.
- Added docs/status/just/CI wiring for
  `uv run python scripts/check_horizontal_scaling_readiness.py` and `--self-test`.

## Operator boundary

This story does not implement Story 132.7 or Story 132.8. It does not run load
or failure drills, execute backups or restores, provision hosts, mutate
production hosts, add credentials, publish host ports, configure an external
load balancer, activate runtime audit emitters, or change the default SQLite
single-host runtime path.

## Verification commands

```bash
uv run python scripts/check_horizontal_scaling_readiness.py --self-test
uv run python scripts/check_horizontal_scaling_readiness.py --verbose
uv run pytest -q tests/scripts/test_check_horizontal_scaling_readiness.py
uv run ruff check scripts/check_horizontal_scaling_readiness.py tests/scripts/test_check_horizontal_scaling_readiness.py
uv run ruff format --check scripts/check_horizontal_scaling_readiness.py tests/scripts/test_check_horizontal_scaling_readiness.py
```

## Follow-up sequence

Current status note: Epic 132 and Story 132.8 are now
`readiness-contract-complete_not_live_activation` / done as of Story 132.8 closure evidence.
This artifact remains readiness-only and does not imply live activation.
