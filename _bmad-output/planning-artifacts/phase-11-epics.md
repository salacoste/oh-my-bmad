# Phase 11 Epics — mTLS for Internal Docker Network

> **Phase:** 11
> **Theme:** Transport-layer mutual authentication for internal Docker network
> **ADR-0023:** Accepted 2026-06-09
> **Epic range:** 56–59

---

## Epic 56 — mTLS Package + CA Tooling

**FRs:** FR127, FR128
**NFRs:** NFR-S16 (handshake <10ms)

### Story 56-1: `packages/mtls/` — TLS context factory

**Scope:** Create `packages/mtls/` workspace package with:
- `mtls.py` — `create_ssl_context(role: Literal["server", "client"]) -> ssl.SSLContext | None`
- `certs.py` — cert path resolution from env vars, validation, expiry checking
- `settings.py` — `MTLSSettings` pydantic-settings model reading `MTLS_ENABLED`, `MTLS_CERT_PATH`, `MTLS_KEY_PATH`, `MTLS_CA_PATH`
- `pyproject.toml` — workspace member, deps: no new external deps
- `tests/test_mtls.py` — unit tests with `cryptography`-generated test certs
- `tests/test_settings.py` — config binding tests

**AC:**
- `create_ssl_context("server")` returns `SSLContext` with `CERT_REQUIRED`
- `create_ssl_context("client")` returns `SSLContext` with client cert + CA loaded
- Returns `None` when `MTLS_ENABLED` is unset/false
- Raises `MTLSConfigError` on missing/invalid/expired cert paths
- ~30 unit tests covering valid/expired/untrusted/mismatched scenarios
- All tests use `cryptography`-generated fixtures (no committed certs)
- `ruff check` clean, mypy clean

### Story 56-2: `omb-ca` CLI tool

**Scope:** Create `scripts/omb-ca/` with:
- `__main__.py` — CLI entry point with subcommands: `init`, `issue`, `rotate`, `check`
- `ca.py` — CA key generation, cert signing, cert validation
- Uses `cryptography` library (already in deps)

**AC:**
- `omb-ca init` creates `ca.pem` + `ca-key.pem` in `./certs/`
- `omb-ca issue <service>` creates `<service>.pem` + `<service>-key.pem` with SAN
- Certs have 72h validity, SAN matches Docker service name
- `omb-ca rotate <service>` creates new cert alongside old one
- `omb-ca check` returns non-zero if any cert expires within 24h
- Idempotent: re-running is safe
- Unit tests for all commands

---

## Epic 57 — Service Integration (Server + Client TLS)

**FRs:** FR129, FR130, FR131

### Story 57-1: Server-side TLS for core HTTP services

**Scope:** Add TLS support to registry-api, telegram-gateway, metrics-subscriber:
- Load TLS context from `packages/mtls/` in service startup
- Pass `ssl=` parameter to uvicorn
- Update health probes for TLS
- Log TLS status at startup

**AC:**
- Services start with TLS when `MTLS_ENABLED=true`
- Connections without valid client cert rejected
- Health probes work over TLS
- Falls back to plain HTTP when `MTLS_ENABLED` unset
- TLS status logged at startup (NFR-O20)

### Story 57-2: Server-side TLS for remote MCP services

**Scope:** Update all 9 `__main__.py` files:
- Add TLS context to `_run_streamable_http()` when `MTLS_ENABLED=true`
- TLS handshake first, then JWT bearer token middleware

**AC:**
- `_run_streamable_http()` passes TLS context when enabled
- Combined TLS + JWT validation works
- Falls back to plain streamable-http when disabled
- All 9 servers updated consistently

### Story 57-3: Client-side TLS for mcp_clients

**Scope:** Update both `mcp_clients.py` (worker-wrapper + orchestrator-adapter):
- Pass TLS client context to `httpx.AsyncClient` when `MTLS_ENABLED=true`
- Client presents its own cert for mTLS
- Update tests with TLS fixtures

**AC:**
- `httpx.AsyncClient` constructed with `verify=ssl_context` when TLS active
- Client cert presented for mTLS
- Rejects servers with untrusted/expired certs
- Existing tests pass without TLS env vars
- New TLS-specific tests with generated fixtures

---

## Epic 58 — CI Gates + Compose Profile

**FRs:** FR132, FR133

### Story 58-1: Extend `check_mcp_transport.py` for mTLS

**Scope:**
- Add mTLS enforcement checks to existing transport gate
- When `mtls` profile active, validate all network services use TLS
- New self-test fixtures for mTLS scenarios

**AC:**
- `check_mcp_transport.py --self-test` passes with mTLS fixtures
- Gate validates TLS config consistency
- Existing checks unchanged

### Story 58-2: New secrets hygiene gate

**Scope:** Create `scripts/check_no_secrets.py`:
- Reject committed `.pem`, `.key`, `.crt`, `.p12` files
- Check for hardcoded cert paths in source code
- Self-test fixtures

**AC:**
- Gate rejects committed cert/key files
- Gate rejects hardcoded cert paths
- Self-test passes
- Added to discipline scripts

### Story 58-3: Docker compose `mtls` profile

**Scope:** Add `mtls` compose profile to `docker-compose.yml`:
- Cert volume mount `./certs/:/certs:ro`
- `MTLS_ENABLED=true` + cert path env vars per service
- Works with `--profile remote-mcp --profile mtls`
- No external ports

**AC:**
- `docker compose --profile mtls config` validates
- `docker compose --profile remote-mcp --profile mtls config` validates
- No external port mappings
- Cert volume read-only in all services

---

## Epic 59 — Validation + Retrospective

### Story 59-1: Full CI validation

**Scope:**
- Run all discipline scripts
- Verify 3205+ existing tests pass without TLS env vars
- Verify new mTLS tests pass
- Docker compose profile validation
- No-TLS regression gate

**AC:**
- All discipline scripts exit 0
- All existing tests pass without TLS config
- All new mTLS tests pass
- Compose profiles validate
- No committed cert material

### Story 59-2: Phase 11 retrospective

**Scope:** Produce retrospective covering:
- FR/NFR mapping
- Invariant mapping
- Lessons learned
- Carry-forward items

**AC:**
- Retrospective document produced
- Sprint status updated
- All epics marked `done`

---

## Story count summary

| Epic | Stories |
|------|---------|
| 56 — mTLS Package + CA Tooling | 2 |
| 57 — Service Integration | 3 |
| 58 — CI Gates + Compose Profile | 3 |
| 59 — Validation + Retrospective | 2 |
| **Total** | **10** |
