# Story 132.2 — remote Postgres production mode readiness

## Summary

Story 132.2 adds a readiness contract and executable checker for bounded, opt-in remote Postgres production-mode support evidence. The slice records the required production-readiness shape without live activation: SQLite remains the default, remote Postgres selection stays explicit through `REGISTRY_DATABASE_URL`, and production activation remains deferred/fail-closed until later operator evidence supplies credentials, provisioning, migration, backup/restore, DB mTLS, and approval records.

No live activation, provisioning, credentials, compose profile, deployment target, production host mutation, runtime audit emitter, or production DSN value is added by this slice.

## Contract and checker

- Contract: `docs/remote-postgres-production-readiness.json`
- Checker: `scripts/check_remote_postgres_readiness.py`
- Tests: `tests/scripts/test_check_remote_postgres_readiness.py`
- Local gate:

```bash
uv run python scripts/check_remote_postgres_readiness.py
uv run python scripts/check_remote_postgres_readiness.py --self-test
```

The checker validates contract shape, docs refs and anchors, docs/status mentions, justfile and CI wiring, redaction and secret-like value rejection, overclaim prevention, and stable runtime strings where reasonable.

## Required readiness evidence

- SQLite default preservation and explicit opt-in remote Postgres selection.
- Exact bounded pool contract: `pool_size = 5 + 2 * num_workers`, `max_overflow = 5`, `pool_timeout = 30`, `pool_recycle = 1800`, and pre-ping enabled.
- Alembic strategy with exactly one migration runner, pre-migration backup evidence, revision evidence, and rollback/fix-forward decision records.
- Epic 133 DB mTLS composition through `REGISTRY_DB_MTLS_ENABLED` without plaintext fallback.
- Backup/restore drill evidence including checksum verification, integrity/consistency checks, restored scratch target identity, Alembic revision parity, and rollback/fix-forward outcomes.
- Registry-api read-side support contract without making registry-api a second materializer or migration runner.
- Redaction for diagnostics and readiness artifacts: no passwords, full DSNs, private keys, full paths, production hostnames, certificate subjects, or SAN hostnames.

## Verification

Local verification commands for this implementation slice:

```bash
uv run python scripts/check_remote_postgres_readiness.py --self-test
uv run python scripts/check_remote_postgres_readiness.py --verbose
uv run pytest tests/scripts/test_check_remote_postgres_readiness.py -q
```
