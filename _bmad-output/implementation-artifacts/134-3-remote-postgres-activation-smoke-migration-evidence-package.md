# Story 134.3 Remote Postgres Activation Smoke and Migration Evidence Package

## Summary

Story 134.3 is complete locally as a docs/status/static-checker evidence-contract slice for future/operator-gated remote Postgres activation smoke and migration evidence. It defines required future proof for migration preconditions, single migration runner, backup/restore checkpoint, bounded pool settings, writer authority, read-side compatibility, redacted database endpoint identity, and rollback/fix-forward criteria.

No remote Postgres activation is performed or claimed. No migration execution is performed or claimed. No live database cutover, provisioning, production host mutation, compose/profile activation, credential handling, certificate material handling, plaintext fallback, runtime behavior change, dependency change, lockfile change, or production-state change is performed.

## Artifacts

- docs/remote-postgres-activation-smoke-migration-evidence.json
- scripts/check_remote_postgres_activation_smoke_migration_evidence.py
- tests/scripts/test_check_remote_postgres_activation_smoke_migration_evidence.py
- _bmad-output/implementation-artifacts/134-3-remote-postgres-activation-smoke-migration-evidence-package.md

## Prerequisite references

- Epic 132 remote Postgres readiness: docs/remote-postgres-production-readiness.json, docs/registry-remote-postgres-profile-readiness.json, and docs/split-deployment-remote-postgres-closure-readiness.json.
- Epic 133 DB mTLS readiness: docs/db-mtls-readiness.json.
- Story 134.1 controlled activation evidence: docs/controlled-activation-evidence.json and _bmad-output/implementation-artifacts/134-1-activation-evidence-schema-preflight-gate.md.
- Story 134.2 split-deployment smoke evidence: docs/split-deployment-activation-smoke-evidence.json and _bmad-output/implementation-artifacts/134-2-split-deployment-activation-smoke-evidence-package.md.

These references are prerequisites only and are not proof activation occurred.

## Redaction and secret boundary

Future evidence must be redacted. Credential values, token values, private key material, certificate material, unredacted connection strings, unredacted database endpoint identity, production host secrets, production hostnames, and plaintext fallback evidence are forbidden.

## Verification commands

python -m json.tool docs/remote-postgres-activation-smoke-migration-evidence.json >/dev/null
uv run python scripts/check_remote_postgres_activation_smoke_migration_evidence.py --self-test
uv run python scripts/check_remote_postgres_activation_smoke_migration_evidence.py
uv run pytest tests/scripts/test_check_remote_postgres_activation_smoke_migration_evidence.py
uv run ruff check scripts/check_remote_postgres_activation_smoke_migration_evidence.py tests/scripts/test_check_remote_postgres_activation_smoke_migration_evidence.py
uv run ruff format --check scripts/check_remote_postgres_activation_smoke_migration_evidence.py tests/scripts/test_check_remote_postgres_activation_smoke_migration_evidence.py

## Gate wiring

The checker is wired into just lint, just check-gates, just check-gates-self-test, and CI static/self-test checks via:

- uv run python scripts/check_remote_postgres_activation_smoke_migration_evidence.py
- uv run python scripts/check_remote_postgres_activation_smoke_migration_evidence.py --self-test
