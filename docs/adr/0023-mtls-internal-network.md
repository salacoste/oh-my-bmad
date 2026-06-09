---
id: ADR-0023
status: accepted
date: 2026-06-09
supersedes: null
amends: null
---

# ADR-0023: mTLS for Internal Docker Network

## Status

**Accepted** — 2026-06-09. Gates Phase 11 (mTLS for internal Docker network). Must be `accepted` before any code change that creates TLS contexts or generates certificates.

## Context

All services in the oh-my-bmad platform communicate on a single Docker bridge network (`oh-my-bmad-net`). Phase 10 (ADR-0022) added JWT bearer token auth for MCP streamable-http transport — application-layer (L7) authentication. However, zero transport-layer (L4) authentication exists between services. Any container on the bridge network can reach any other container's HTTP endpoint without proving its identity.

The architecture document's "Future work beyond Phase 10" (`docs/architecture.md`) lists mTLS as the next priority security item. Phase 10's ADR-0022 Decision 6 (OAuth 2.1 future) noted that bearer token is sufficient for single-operator but transport-layer hardening is the natural next step.

The deployment topology is a single Docker host with 7 core services + 9 remote MCP profile services. All services are Python (uvicorn/httpx/FastAPI). The `cryptography` library is already a dependency (used by PyJWT).

## Decision

### Decision 1 — Self-managed CA via `omb-ca` CLI

A private certificate authority managed by a CLI tool (`scripts/omb-ca/`). No external CA infrastructure (step-ca, Vault, cert-manager). The CA key stays on the host filesystem, never enters a container.

- `omb-ca init` — generates root CA key + certificate
- `omb-ca issue <service>` — generates per-service cert signed by CA
- `omb-ca rotate <service>` — generates new cert with overlap window
- `omb-ca check` — validates all certs, warns on near-expiry

Justification: Single-host, single-operator topology does not justify distributed CA infrastructure. `cryptography` library already available. Zero new external dependencies.

### Decision 2 — Service-level Python SSL contexts

Each service creates its own `ssl.SSLContext` using Python's stdlib `ssl` module, wrapped in a shared `packages/mtls/` package. No sidecar proxies (Envoy, etc.).

Justification: All 7 services are Python using uvicorn (native SSL support) and httpx (native SSL support). Adding sidecars means 7+ new containers, 7+ configs, and a separate operational surface. Service-level SSL requires ~30 lines of wrapper code per service via a shared package.

### Decision 3 — End-to-end TLS termination

Each service terminates TLS independently. No centralized reverse proxy for TLS termination.

Justification: On a single-host bridge network there are zero intermediate hops. End-to-end mTLS means every hop authenticates every other hop. A reverse proxy would create a trusted zone behind it, defeating the threat model.

### Decision 4 — Profile-gated activation

mTLS activates via a Docker Compose profile: `docker compose --profile mtls up`. When the `mtls` profile is not active, behavior is identical to Phase 10.

Justification (Mary's middle ground): The threat model for mTLS on a single Docker host is real but narrow (any realistic compromise path grants the attacker valid mTLS credentials). Profile-gating ships the full implementation while deferring the operational tax until split deployment or compliance requirements justify it.

### Decision 5 — mTLS complements, does not replace, JWT

When both `mtls` and `remote-mcp` profiles are active: TLS handshake (L4) validates service identity, then JWT bearer token (L7) validates request context. Both layers are enforced.

Justification: mTLS authenticates the *service* (container A proves it is container A). JWT authenticates the *request* (who authorized this call, what scope). Neither subsumes the other.

### Decision 6 — Network services only, not stdio pipes

mTLS applies only to services communicating over the Docker network (HTTP/TCP). Stdio MCP subprocesses (spawned by worker-wrapper within the same container) are excluded.

Justification: Stdio pipes are local, process-isolated, and within the same container. TLS on pipes solves a nonexistent problem. The right lever for stdio security is Unix file permissions and Docker process namespace isolation.

### Decision 7 — Postgres mTLS deferred to Phase 11.1

Service-to-service HTTP mTLS ships first. Postgres connection mTLS (`sslmode=verify-full` with client certs) follows after the core infrastructure proves stable.

Justification: Postgres mTLS touches connection strings, poolers, and potentially Postgres server config — a different operational surface. Ship the simpler case first.

## Consequences

### Positive

- **Defense-in-depth.** L4 transport auth + L7 application auth.
- **Split deployment enabler.** mTLS is a prerequisite for services leaving the shared Docker network.
- **Zero external dependencies.** stdlib `ssl` + `cryptography` (already in deps).
- **Profile-gated.** Full implementation ships without forcing operational tax on single-host operators.
- **Short-lived certs.** 72h validity with 24h rotation minimizes blast radius of cert compromise.

### Negative

- **Certificate lifecycle management.** Generation, distribution, rotation, monitoring. Mitigated by `omb-ca` CLI automation.
- **New failure modes.** Expired certs = service degradation. Mitigated by short validity + automated rotation + monitoring.
- **Debugging complexity.** `curl` against internal endpoints requires cert flags. Mitigated by clear error messages.
- **Increased Docker Compose surface.** New profile + volume mounts. Mitigated by validation tests.

## Alternatives considered

- **Envoy sidecar mesh.** Rejected — overkill for 7 services on a single host. Adds 7+ containers, 7+ configs, separate operational surface, and TLS termination at the sidecar (not end-to-end).
- **step-ca / HashiCorp Vault.** Rejected — distributed CA systems to manage; architectural overkill for single-operator, single-host deployment. Brings databases, APIs, and backup requirements.
- **Reverse proxy termination (nginx/Traefik).** Rejected — creates trusted zone behind proxy where any container can impersonate any service. Defeats end-to-end auth.
- **mTLS on stdio pipes.** Rejected — solves nonexistent problem. Stdio subprocesses are local, process-isolated, within the same container.
- **Long-lived certificates (365 days).** Rejected — create false sense of security. Missed rotation gives 364 days of exposure instead of 48 hours.

## Linked artifacts

- ADR-0022 — Remote MCP Transport (JWT bearer token auth, the L7 complement)
- `docs/architecture.md` — "Future work beyond Phase 10" lists mTLS as priority
- `packages/mcp_auth/` — JWT auth middleware (unchanged, complementary)
- `packages/mtls/` — new TLS context factory package

— *R2d2, 2026-06-09.*
