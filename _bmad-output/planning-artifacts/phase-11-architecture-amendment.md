## Phase 11 Architecture Amendment — mTLS for Internal Docker Network

> **Amendment added:** 2026-06-09.
>
> **Companion documents:**
> - PRD amendment: see [`phase-11-prd-amendment.md`](./phase-11-prd-amendment.md) (FR127–FR133).
> - mTLS ADR: see [`docs/adr/0023-mtls-internal-network.md`](../../docs/adr/0023-mtls-internal-network.md).
> - Prior amendment: [`phase-10-architecture-amendment.md`](./phase-10-architecture-amendment.md) (P10-I1, P10-I2).

**Theme.** Transport-layer mutual authentication for all internal Docker-network service-to-service communication. Phase 11 adds mTLS below Phase 10's JWT bearer token (L4 transport auth beneath L7 application auth), establishing defense-in-depth. Profile-gated activation — single-host deployments opt in; split deployments require it. Default deployments remain identical to Phase 10.

### Architectural decisions (Party Mode consensus — Winston, Amelia, Murat, Mary)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| TLS enforcement level | Service-level Python `ssl` module | No sidecars; 7 controlled services; ~30 lines wrapper code each; native uvicorn/httpx support |
| Certificate authority | Self-managed `omb-ca` CLI tool | Single-host topology; `cryptography` already in deps; no daemons; CA key stays on host |
| TLS termination | End-to-end per-service | Every hop authenticates; no reverse proxy on single-host bridge network |
| Scope (Phase 11.0) | Network-facing services only (7 core + 9 remote MCP profile) | Stdio MCP subprocesses communicate over pipes, not network; TLS on pipes solves nonexistent problem |
| mTLS + JWT interaction | Complementary (L4 + L7) | mTLS authenticates service identity; JWT authenticates request context; neither subsumes the other |
| Postgres mTLS | Deferred to Phase 11.1 | Different operational surface; ship service-to-service first |
| Certificate rotation | Short-lived certs (72h), overlap window, SIGHUP reload | Automated from day one; no human-in-the-loop rotation |
| Activation | Profile-gated (`mtls` compose profile) | Mary's middle ground: full implementation ships, activation deferred until split deployment or compliance requires it; single-host operators pay zero tax |
| CI gate | Extend `check_mcp_transport.py` + new secrets hygiene gate | Transport is transport; one gate, one place; hygiene gate catches committed certs |
| Test fixtures | Generated at test time via `cryptography` | No committed certs (they expire); parametrize over valid/expired/untrusted |
| Package structure | New `packages/mtls/` (separate from `packages/mcp_auth/`) | Transport-layer ≠ application-layer; separate concerns |

### Preserved invariants (Phase 1 through Phase 10 carry forward)

All prior invariants stand unchanged. As they apply to the new surface:

- **Single-writer (FR26, P2-I1).** mTLS does not affect persistence.
- **Service-to-service imports banned.** mTLS is a shared package, not cross-service imports.
- **Capability-tier enforcement.** mTLS is transport-layer; tiers are application-layer. Orthogonal.
- **Remote MCP auth required (P10-I2).** mTLS adds transport auth ON TOP of JWT. Both required when both active.
- **Zero-change backward compatibility (NFR-M10).** Default deployment = Phase 10 behavior.

### New invariants (Phase 11)

| # | Invariant | Why |
|---|-----------|-----|
| **P11-I1** | **mTLS all-or-nothing within a profile.** When the `mtls` compose profile is active, ALL network-facing services MUST present valid client certificates. No "TLS-optional" mode. Partial TLS config is a startup error, not a silent fallback. | Partial mTLS is worse than no mTLS — it creates false confidence. Tested by CI gate. |
| **P11-I2** | **No committed certificate or key material.** `.pem`, `.key`, `.crt`, `.p12` files are forbidden in the source tree. CI gate enforces. Certs generated at deploy time (prod) or test time (CI). | Committed certs expire, create false confidence, and may leak private keys. Secrets hygiene gate enforces. |
| **P11-I3** | **Short-lived certificates only.** Maximum certificate validity: 72 hours. Rotation interval: 24 hours. | Long-lived certs create a false sense of security and make rotation failures harder to detect. |

### ADR-0023: mTLS for Internal Docker Network

**Status:** Accepted — 2026-06-09.

**Decision:**
1. Self-managed CA via `omb-ca` CLI tool (no external dependencies).
2. Service-level Python SSL contexts via `packages/mtls/` (no sidecars).
3. End-to-end TLS termination at each service (no centralized proxy).
4. Profile-gated activation: `docker compose --profile mtls up` enables mTLS.
5. When `mtls` profile active: all HTTP services present client certs, verify peers.
6. When `mtls` profile inactive: behavior identical to Phase 10 (zero change).
7. Certificate generation: `omb-ca init` creates CA; `omb-ca issue <service>` creates per-service certs.
8. Postgres connection mTLS deferred to Phase 11.1.

**Alternatives rejected:**
- Envoy sidecar mesh — overkill for 7 services on a single host; adds 7 containers, 7 configs, separate operational surface.
- step-ca / HashiCorp Vault — distributed systems to manage; architectural overkill for single-operator deployment.
- Reverse proxy termination — defeats the threat model (creates trusted zone behind proxy).
- mTLS on stdio pipes — solves a nonexistent problem; stdio MCP subprocesses are local and process-isolated.

**Consequences:**
- Positive: defense-in-depth (L4 + L7 auth); enables split deployment; zero external dependencies.
- Negative: cert lifecycle management (mitigated by automation); debugging complexity (mitigated by clear error messages); onboarding friction (mitigated by `omb-ca` tooling).

### Component inventory changes

| Component | Workspace member | Role | New in Phase 11 |
|-----------|-----------------|------|-----------------|
| `packages/mtls/` | New | TLS context factory + cert path resolution | Yes |
| `omb-ca` CLI | `scripts/omb-ca` | CA init + cert issue + rotation | Yes |
| `packages/mcp_auth/` | Existing | JWT bearer token middleware (unchanged) | No |
| All 9 MCP `__main__.py` | Existing | Add TLS context to streamable-http | Modified |
| 2× `mcp_clients.py` | Existing | Add TLS client context | Modified |
| `docker-compose.yml` | Existing | `mtls` profile + cert volume mounts | Modified |
| `check_mcp_transport.py` | Existing | Extend with TLS enforcement checks | Modified |

— *Amendment by R2d2, 2026-06-09, via the BMad planning workflow (Phase 11 scoping + Party Mode consensus).*
