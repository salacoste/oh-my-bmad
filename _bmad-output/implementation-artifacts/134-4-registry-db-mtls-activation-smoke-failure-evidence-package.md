# Story 134.4 Registry DB mTLS Activation Smoke and Failure Evidence Package

## Summary

Story 134.4 is complete locally as a docs/status/static-checker evidence-contract slice for future/operator-gated registry DB mTLS activation smoke and failure evidence. It defines required future proof for explicit operator gate enablement, server-side TLS enforcement, client certificate enforcement, approved secret location identifiers, certificate expiry/revocation/hostname metadata, no-plaintext fallback behavior, bounded sanitized failure diagnostics, and rollback/fail-closed criteria.

No registry DB mTLS activation is performed or claimed. No real certificate material, private key material, credential values, production activation, provisioning, production host mutation, compose/profile activation, plaintext fallback, runtime behavior change, dependency change, lockfile change, or production-state change is performed.

## Artifacts

- docs/registry-db-mtls-activation-smoke-failure-evidence.json
- scripts/check_registry_db_mtls_activation_smoke_failure_evidence.py
- tests/scripts/test_check_registry_db_mtls_activation_smoke_failure_evidence.py
- _bmad-output/implementation-artifacts/134-4-registry-db-mtls-activation-smoke-failure-evidence-package.md

## Prerequisite references

- Epic 133 DB mTLS readiness: docs/db-mtls-readiness.json and _bmad-output/implementation-artifacts/133-5-db-mtls-closure-evidence.md.
- Story 134.1 controlled activation evidence: docs/controlled-activation-evidence.json and _bmad-output/implementation-artifacts/134-1-activation-evidence-schema-preflight-gate.md.
- Story 134.2 split-deployment smoke evidence: docs/split-deployment-activation-smoke-evidence.json and _bmad-output/implementation-artifacts/134-2-split-deployment-activation-smoke-evidence-package.md.
- Story 134.3 remote Postgres smoke/migration evidence: docs/remote-postgres-activation-smoke-migration-evidence.json and _bmad-output/implementation-artifacts/134-3-remote-postgres-activation-smoke-migration-evidence-package.md.

These references are prerequisites only and are not proof activation occurred.

## Redaction and secret boundary

Future evidence must be redacted. Credential values, token values, private key material, certificate bodies, unredacted connection strings, production host secrets, production hostnames, certificate subject material, full secret paths, and plaintext fallback evidence are forbidden.

## Verification commands

python -m json.tool docs/registry-db-mtls-activation-smoke-failure-evidence.json >/dev/null
uv run python scripts/check_registry_db_mtls_activation_smoke_failure_evidence.py --self-test
uv run python scripts/check_registry_db_mtls_activation_smoke_failure_evidence.py
uv run pytest tests/scripts/test_check_registry_db_mtls_activation_smoke_failure_evidence.py
uv run ruff check scripts/check_registry_db_mtls_activation_smoke_failure_evidence.py tests/scripts/test_check_registry_db_mtls_activation_smoke_failure_evidence.py
uv run ruff format --check scripts/check_registry_db_mtls_activation_smoke_failure_evidence.py tests/scripts/test_check_registry_db_mtls_activation_smoke_failure_evidence.py

## Gate wiring

The checker is wired into just lint, just check-gates, just check-gates-self-test, and CI static/self-test checks via:

- uv run python scripts/check_registry_db_mtls_activation_smoke_failure_evidence.py
- uv run python scripts/check_registry_db_mtls_activation_smoke_failure_evidence.py --self-test
