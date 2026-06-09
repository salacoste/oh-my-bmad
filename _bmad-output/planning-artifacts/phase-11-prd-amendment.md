# Phase 11 Scope Extension — mTLS for Internal Docker Network

> **Status:** Phase-11 PRD amendment. Adds transport-layer mutual authentication for all internal Docker-network service-to-service communication. FR/NFR numbering continues (FR127–FR133; NFR-S15–S16, NFR-M11, NFR-R16, NFR-O20). Epic numbering starts at 56.
>
> **Selected via:** ADR-0023 accepted 2026-06-09. Architecture document's "Future work beyond Phase 10" lists mTLS as priority item. Party Mode consensus (Winston/Amelia/Murat/Mary).

**Theme:** the **mTLS layer** — transport-layer mutual authentication for internal Docker network communication, establishing defense-in-depth below Phase 10's JWT bearer token.

**Resolved scope:**

- **IN.** `packages/mtls/` — TLS context factory (server + client), cert path resolution, validation
- **IN.** `omb-ca` CLI tool — CA initialization, per-service cert issuance, rotation
- **IN.** Service-level mTLS for all network-facing services (7 core + 9 remote MCP profile)
- **IN.** Profile-gated activation (`mtls` compose profile)
- **IN.** CI gates — extend `check_mcp_transport.py` + new secrets hygiene gate
- **IN.** Test fixture strategy — certs generated at test time via `cryptography`
- **OUT.** Postgres connection mTLS (Phase 11.1), stdio MCP subprocess mTLS, sidecar mesh, external CA tools

**Preserved invariants (carry from Phases 1–10 — non-negotiable):**

- All prior invariants stand unchanged (P1-I1 through P10-I2).
- JWT bearer token auth (Phase 10) is NOT replaced — mTLS adds L4 beneath L7.
- Zero-change backward compatibility when `mtls` profile not active.

---

## Phase 11 Functional Requirements

### Alpha — TLS package + CLI tooling (Epic 56)

- **FR127.** Mutual TLS context factory. New `packages/mtls/` workspace package provides `create_ssl_context(role: Literal["server", "client"]) -> ssl.SSLContext`. Server context sets `verify_mode = CERT_REQUIRED`, loads cert/key/ca. Client context loads ca for server verification + client cert/key for presentation. When `MTLS_ENABLED` is false/unset, returns None (caller uses plain HTTP). All config from env vars: `MTLS_CERT_PATH`, `MTLS_KEY_PATH`, `MTLS_CA_PATH`.

  **Acceptance criteria:**
  - `create_ssl_context("server")` returns `ssl.SSLContext` with `CERT_REQUIRED`
  - `create_ssl_context("client")` returns `ssl.SSLContext` with client cert loaded
  - Returns `None` when `MTLS_ENABLED` is unset/false
  - Raises `MTLSConfigError` with actionable message on missing/invalid cert paths
  - Detects expired certs at context creation time
  - No new external dependencies (stdlib `ssl` + `cryptography`)

- **FR128.** Certificate management CLI (`omb-ca`). Scripts in `scripts/omb-ca/` providing:
  - `omb-ca init` — generate root CA key + cert
  - `omb-ca issue <service-name>` — generate per-service cert signed by CA, with SAN matching service name
  - `omb-ca rotate <service-name>` — generate new cert, keep old valid during overlap window
  - `omb-ca check` — verify all certs are valid and not near expiry
  Certs valid for 72 hours, rotated every 24 hours. Output to `./certs/` directory.

  **Acceptance criteria:**
  - `omb-ca init` creates `ca.pem` + `ca-key.pem`
  - `omb-ca issue task-registry` creates `task-registry.pem` + `task-registry-key.pem`
  - Issued certs have SAN matching service Docker container name
  - `omb-ca check` returns non-zero if any cert expires within 24 hours
  - Idempotent: re-running `init` or `issue` is safe

### Beta — Service integration (Epic 57)

- **FR129.** Server-side TLS for HTTP services. Each service that runs an HTTP server (registry-api, telegram-gateway, metrics-subscriber) loads TLS context from `packages/mtls/` when `MTLS_ENABLED=true`. Passes to uvicorn as `ssl=` parameter. Health probes work over TLS.

  **Acceptance criteria:**
  - Uvicorn starts with TLS context when `MTLS_ENABLED=true`
  - Connections without valid client cert rejected (TLS alert)
  - Health probes (`/healthz`, TCP socket check) work over TLS
  - Falls back to plain HTTP when `MTLS_ENABLED` unset
  - TLS status logged at startup

- **FR130.** Server-side TLS for streamable-http MCP services. All 9 remote MCP profile services load TLS context in `_run_streamable_http()`. Works with `--profile remote-mcp --profile mtls`.

  **Acceptance criteria:**
  - `_run_streamable_http()` passes TLS context when `MTLS_ENABLED=true`
  - Combined with JWT bearer token middleware: TLS handshake first, then JWT validation
  - Falls back to plain streamable-http when `MTLS_ENABLED` unset

- **FR131.** Client-side TLS. Both `mcp_clients.py` (worker-wrapper + orchestrator-adapter) pass TLS client context to `httpx.AsyncClient` when `MTLS_ENABLED=true`. Also applies to any service making HTTP calls to other services (telegram-gateway → registry-api, etc.).

  **Acceptance criteria:**
  - `httpx.AsyncClient` constructed with `verify=ssl_context` when TLS active
  - Client presents its own cert for mTLS
  - Rejects servers with untrusted or expired certs
  - Falls back to plain HTTP client when `MTLS_ENABLED` unset

### Gamma — CI gates + compose profile (Epic 58)

- **FR132.** CI gate extension. `check_mcp_transport.py` gains mTLS enforcement checks: when `mtls` profile is active, all network services must use TLS; no plaintext fallback path exists in compose config. New secrets hygiene gate (`scripts/check_no_secrets.py`) rejects committed `.pem`, `.key`, `.crt`, `.p12` files.

  **Acceptance criteria:**
  - `check_mcp_transport.py` validates TLS config consistency
  - `check_no_secrets.py` rejects committed cert/key files
  - Self-test fixtures for both gates
  - Existing 3205 tests pass without any TLS env vars (regression guard)

- **FR133.** Docker compose `mtls` profile. New compose profile mounts cert volume read-only, sets `MTLS_ENABLED=true` + cert path env vars for each service. Works with `--profile remote-mcp --profile mtls`. No external ports exposed.

  **Acceptance criteria:**
  - `docker compose --profile mtls config` validates
  - `docker compose --profile remote-mcp --profile mtls config` validates
  - Cert volume mounted read-only in all services
  - No external port mappings (NFR-S13 preserved)

## Phase 11 Non-Functional Requirements

- **NFR-S15 (mTLS profile-gated, default off).** mTLS activates ONLY with `--profile mtls`. Default deployment = identical to Phase 10. Zero-change backward compatibility.
- **NFR-S16 (TLS handshake latency).** TLS handshake with local certs completes in <10ms p99. No OCSP/CRL network access.
- **NFR-M11 (Zero-change backward compatibility).** Every new code path guarded by `MTLS_ENABLED`. All 3205 existing tests pass without any mTLS config.
- **NFR-R16 (Clear failure on misconfiguration).** Missing/expired/invalid certs produce actionable error messages, not silent plaintext fallback. Services fail to start rather than start insecure.
- **NFR-O20 (TLS observability).** TLS status (on/off, cert CN, cert expiry) logged at service startup. Available in health probes.

## Phase 11 Invariants

- **P11-I1: mTLS all-or-nothing within profile.** When `mtls` profile active, ALL network services MUST present valid client certs. No partial TLS.
- **P11-I2: No committed cert/key material.** `.pem`, `.key`, `.crt`, `.p12` forbidden in source tree.
- **P11-I3: Short-lived certificates only.** Max 72h validity, 24h rotation interval.

## Phase 11 Architecture Decisions Required

- **ADR-0023: mTLS for internal Docker network** — accepted 2026-06-09
- **ADR-0024: Phase 11 gate** — to be created after epics decomposed

## Phase 11 Ship-Blocker Checklist

1. [ ] All Phase 1–10 invariants regression-free
2. [ ] ADR-0023 accepted
3. [ ] `packages/mtls/` imports cleanly, `create_ssl_context()` works
4. [ ] `omb-ca init` + `omb-ca issue` generate valid certs
5. [ ] Default deployment (no profiles) = identical to Phase 10
6. [ ] `just lint` EXIT 0
7. [ ] All discipline scripts exit 0
8. [ ] No new third-party dependencies
9. [ ] `check_mcp_transport.py --self-test` passes (extended)
10. [ ] `check_no_secrets.py` passes (new gate)
11. [ ] 3205+ existing tests pass without TLS env vars
12. [ ] New mTLS unit tests pass (~30 tests)
13. [ ] docker compose `mtls` profile validates
14. [ ] Phase 11 retrospective produced

— *Amendment by R2d2, 2026-06-09, via the BMad planning workflow (Phase 11 scoping + Party Mode consensus).*
