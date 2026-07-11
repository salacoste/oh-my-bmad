# Story 134.5 Combined Split/Remote Postgres/DB mTLS Rehearsal Evidence Package

## Summary

Story 134.5 is complete locally as a docs/status/static-checker evidence-contract slice for future/operator-gated combined split deployment, remote Postgres, and DB mTLS rehearsal evidence. It defines required future proof for explicit operator gate enablement, split-service placement, remote Postgres endpoint and migration preconditions, DB mTLS server/client-certificate enforcement, combined smoke traces, backup/restore checkpoint references, no-plaintext fallback behavior, bounded sanitized failure-injection diagnostics, rollback and emergency-disable criteria, fail-closed status, independent review, and go/no-go signoff.

No live combined rehearsal is performed or claimed. No split deployment activation, remote Postgres activation, DB mTLS production activation, migration execution, live database cutover, real certificate material, private key material, credential values, production activation, provisioning, production host mutation, compose/profile activation, plaintext fallback, runtime behavior change, operator/deployment/rollback/restore/migration/activation/production script change, dependency change, lockfile change, or production-state change is performed. Static checker/test/CI gate wiring is local validation only and does not execute activation or rehearsal scripts.

## Artifacts

- docs/combined-split-remote-postgres-db-mtls-rehearsal-evidence.json
- scripts/check_combined_split_remote_postgres_db_mtls_rehearsal_evidence.py
- tests/scripts/test_check_combined_split_remote_postgres_db_mtls_rehearsal_evidence.py
- _bmad-output/implementation-artifacts/134-5-combined-split-remote-postgres-db-mtls-rehearsal-evidence-package.md

## Prerequisite references

- Epic 132 split deployment and remote Postgres closure readiness: docs/split-deployment-remote-postgres-closure-readiness.json and _bmad-output/implementation-artifacts/132-8-closure-evidence.md.
- Epic 133 DB mTLS readiness: docs/db-mtls-readiness.json and _bmad-output/implementation-artifacts/133-5-db-mtls-closure-evidence.md.
- Story 134.1 controlled activation evidence: docs/controlled-activation-evidence.json and _bmad-output/implementation-artifacts/134-1-activation-evidence-schema-preflight-gate.md.
- Story 134.2 split-deployment smoke evidence: docs/split-deployment-activation-smoke-evidence.json and _bmad-output/implementation-artifacts/134-2-split-deployment-activation-smoke-evidence-package.md.
- Story 134.3 remote Postgres smoke/migration evidence: docs/remote-postgres-activation-smoke-migration-evidence.json and _bmad-output/implementation-artifacts/134-3-remote-postgres-activation-smoke-migration-evidence-package.md.
- Story 134.4 registry DB mTLS smoke/failure evidence: docs/registry-db-mtls-activation-smoke-failure-evidence.json and _bmad-output/implementation-artifacts/134-4-registry-db-mtls-activation-smoke-failure-evidence-package.md.

These references are prerequisites only and are not proof activation or a combined rehearsal occurred.

## Redaction and secret boundary

Future evidence must be redacted. Credential values, token values, private key material, certificate bodies, DSN values, unredacted connection strings, production host secrets, production hostnames, certificate subject material, full secret paths, and plaintext fallback evidence are forbidden.

## Verification commands

python -m json.tool docs/combined-split-remote-postgres-db-mtls-rehearsal-evidence.json >/dev/null
uv run python scripts/check_combined_split_remote_postgres_db_mtls_rehearsal_evidence.py --self-test
uv run python scripts/check_combined_split_remote_postgres_db_mtls_rehearsal_evidence.py
uv run pytest tests/scripts/test_check_combined_split_remote_postgres_db_mtls_rehearsal_evidence.py
uv run ruff check scripts/check_combined_split_remote_postgres_db_mtls_rehearsal_evidence.py tests/scripts/test_check_combined_split_remote_postgres_db_mtls_rehearsal_evidence.py
uv run ruff format --check scripts/check_combined_split_remote_postgres_db_mtls_rehearsal_evidence.py tests/scripts/test_check_combined_split_remote_postgres_db_mtls_rehearsal_evidence.py

## Gate wiring

The checker is wired into just lint, just check-gates, just check-gates-self-test, and CI static/self-test checks via local validation wiring only. This is not an operator/deployment/rollback/restore/migration/activation/production script change:

- uv run python scripts/check_combined_split_remote_postgres_db_mtls_rehearsal_evidence.py
- uv run python scripts/check_combined_split_remote_postgres_db_mtls_rehearsal_evidence.py --self-test
