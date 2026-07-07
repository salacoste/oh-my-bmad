# Story 133.1 DB mTLS static readiness contract

## Summary

Story 133.1 now anchors the DB connection mTLS production-readiness contract for
runtime-gated registry-state Postgres connectivity. It introduces
`docs/db-mtls-readiness.json` and `scripts/check_db_mtls_readiness.py`; runtime
code remains gated by `REGISTRY_DB_MTLS_ENABLED` and does not activate production,
connect to live Postgres, mutate hosts, or commit certificate/private-key material.

## Contract coverage

- CA ownership and server/client certificate profile rules, including server SAN
  and verify-full semantics.
- Approved secret prefixes `/run/secrets/` and `/certs/db/`, test-only fixture
  override policy, canonical realpath enforcement, symlink escape rejection, and
  private-key mode/ownership expectations.
- Enabled-profile fail-closed URL policy: only `postgresql+asyncpg://` is allowed
  when DB mTLS is enabled; SQLite, non-Postgres, non-asyncpg Postgres, insecure
  sslmode values, and plaintext fallback are rejected.
- Exact server-side Postgres operator evidence: `ssl=on`, `ssl_cert_file`,
  `ssl_key_file`, `ssl_ca_file`, CRL config when revocation is claimed,
  `pg_hba.conf hostssl` plus `clientcert`, no plaintext `host` bypass, and
  explicit `sslmode=disable` rejection.
- Rotation/revocation, bounded failure classes, sanitized diagnostics, docs/status
  references, closure fields, justfile wiring, and CI wiring.

## Verification command

```bash
uv run python scripts/check_db_mtls_readiness.py
```

## Boundaries

This artifact is a production-readiness contract for runtime-gated support. Live
Postgres evidence, scanner verdicts, code-review, and UltraQA remain pending gates
before production activation.
