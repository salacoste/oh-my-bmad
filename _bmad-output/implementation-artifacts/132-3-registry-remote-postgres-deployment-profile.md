# Story 132.3 — Registry remote Postgres deployment profile

## Summary

Story 132.3 adds an opt-in registry remote Postgres deployment profile/readiness
slice. The implementation introduces `docker-compose.registry-remote-postgres.yml`,
`docs/registry-remote-postgres-profile-readiness.json`, and
`scripts/check_registry_remote_postgres_profile.py` with pytest coverage. Root
SQLite/local defaults remain unchanged, and live production activation remains
operator-gated/fail-closed.

## What changed

- Added `docker-compose.registry-remote-postgres.yml` as an explicit overlay and
  `registry-remote-postgres` compose profile.
- Required `REGISTRY_DATABASE_URL` during compose interpolation for the profile;
  no DSN or credential value is embedded.
- Wired `registry-state`, `registry-api`, and registry-api idempotency storage to
  the same operator-supplied remote Postgres DSN.
- Disabled `REGISTRY_STATE_AUTO_CREATE_SCHEMA` and
  `REGISTRY_API_AUTO_CREATE_IDEMPOTENCY_SCHEMA` in the profile so Alembic remains
  the migration authority.
- Exposed Epic 133 `REGISTRY_DB_MTLS_*` variables in the profile with DB mTLS
  disabled by default and verify-full/no-plaintext-fallback policy when enabled.
- Added `.env.example` placeholders that keep `REGISTRY_DATABASE_URL` blank and
  preserve the local SQLite default.
- Added docs/status/just/CI wiring for
  `uv run python scripts/check_registry_remote_postgres_profile.py` and its
  `--self-test` mode.

## Operator boundary

This story does not provision live Postgres, create production credentials or
DSNs, mutate production hosts, run migrations, execute backups/restores, activate
DB mTLS in production, or add a runtime production audit emitter. A future
approved activation still requires backup/snapshot evidence, a single migration
runner, Alembic pre/post revision evidence, restore-to-scratch validation,
event-log/materialized-state reconciliation, and rollback/fix-forward evidence.

## Verification commands

```bash
uv run python scripts/check_registry_remote_postgres_profile.py --self-test
uv run python scripts/check_registry_remote_postgres_profile.py --verbose
uv run pytest -q tests/scripts/test_check_registry_remote_postgres_profile.py
```

## Follow-up sequence

Epic 132 remains in progress. Story 132.4 is still the next backlog slice for the
worker/MCP/event-bus split profile; Stories 132.5-132.8 remain backlog until
implemented and verified.
