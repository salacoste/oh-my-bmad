---
id: ADR-0022
status: accepted
date: 2026-06-09
supersedes: null
amends: ADR-0010
---

# ADR-0022: Remote MCP Transport — Streamable HTTP with Bearer Token Auth

## Status

**Accepted** — 2026-06-09. Amends ADR-0010 Decision 1 (stdio-only transport) to permit Streamable HTTP as an opt-in alternative. Gates Phase 10 (remote MCP transport). Must be `accepted` before any code change that imports `mcp.server.streamable_http` or `streamable_http_client`.

## Context

All nine MCP servers (`clawhip-bridge`, `task-registry`, `session-registry`, `git`, `github`, `verification`, `memory`, `artifact`, `browser`) communicate over **stdio only** — spawned as subprocesses by worker-wrapper and orchestrator-adapter (ADR-0010 Decision 1, invariant P2-I4). The `check_mcp_transport.py` CI gate enforces this mechanically: imports of `mcp.server.sse`, `mcp.server.streamable_http`, and their associated names (`SseServerTransport`, `sse_app`, `streamable_http_app`) are rejected across the entire first-party source tree.

The MCP specification (version 2025-03-26) defines two standard transports: **stdio** (local subprocess) and **Streamable HTTP** (remote). The older HTTP+SSE transport (2024-11-05) is deprecated. The Python `mcp` SDK already supports Streamable HTTP natively — `FastMCP.run(transport="streamable-http")` on the server side, `streamable_http_client` on the client side — requiring no new external dependencies.

The architecture document's "Future work beyond Phase 9" list (`docs/architecture.md:246-257`) explicitly lists "Remote MCP transport (HTTP/SSE/streamable)" as the #1 priority future work item, noting it "unlocks split deployment and remote workers." The current single-host, all-stdio model limits the platform to a single Docker host — workers and MCP servers must share a filesystem and process namespace.

This ADR opens the Streamable HTTP transport as an **opt-in alternative** to stdio, while preserving stdio as the default and primary transport for single-host deployments.

## Decision

### Decision 1 — Amend P2-I4: stdio-by-default, streamable-http opt-in

The existing invariant (P2-I4) becomes:

> **MCP stdio-by-default, streamable-http opt-in.** SSE transport is permanently forbidden (`mcp.server.sse` imports rejected). Streamable HTTP is permitted when a server explicitly opts in via `MCP_TRANSPORT=streamable-http` env var. Stdio remains the default.

The `check_mcp_transport.py` CI gate is updated to enforce this: `mcp.server.sse`, `SseServerTransport`, and `sse_app` remain unconditionally forbidden. `mcp.server.streamable_http` and `streamable_http_app` are allowed **only** in designated files: each MCP server's `__main__.py` and `auth/` modules within `mcp-servers/`. The `streamable_http_client` import from `mcp.client.streamable_http` is allowed in the spawner `mcp_clients.py` files. All other source trees remain restricted.

ADR-0010 Decision 1 is amended from "stdio transport only" to "stdio transport by default, streamable-http opt-in."

### Decision 2 — Dual-mode `__main__.py` per server

Each MCP server's `__main__.py` gains a `_resolve_transport()` helper:

- When `MCP_TRANSPORT=streamable-http`: call `mcp.run(transport="streamable-http", host="0.0.0.0", port=<MCP_PORT>)` with bearer-token auth middleware applied.
- When absent or `"stdio"` (default): existing `mcp.run()` — zero change.

The server binary is the same; only the runtime transport changes. This preserves ADR-0010 Decision 7 (ships in base image, not a compose service) — when running on stdio, the behavior is identical to today. When running on streamable-http, the same binary exposes an HTTP endpoint instead.

### Decision 3 — Bearer token auth from `JWT_SECRET_KEY` (Phase 10)

A new `packages/mcp_auth/` workspace package provides an ASGI middleware that validates `Authorization: Bearer <token>` on every Streamable HTTP request:

- Reuses the existing `JWT_SECRET_KEY` + HS256 algorithm (same key material as registry-api JWT auth from Story 6.1+).
- Extracts `actor_id` from the `sub` claim.
- Returns HTTP 401 on missing, invalid, or expired tokens.
- When `JWT_SECRET_KEY` is not set, the middleware is not installed (stdio mode never sees auth).

This is NOT OAuth 2.1 — it is a simpler symmetric-key bearer scheme, consistent with the single-operator deployment model. Docker network isolation (no external port exposure) plus bearer token provides defense-in-depth. OAuth 2.1 with PKCE is tracked as future work (Decision 6).

### Decision 4 — Client-side `streamable_http_client` in `mcp_clients.py`

The `MCPClientGroup._connect()` method gains a transport-mode branch:

- When `<server>_url` is set (e.g., `TASK_REGISTRY_URL=http://omb-task-registry:8081/mcp`): use `streamable_http_client(url, headers={"Authorization": f"Bearer {token}"})`.
- When `<server>_command` is set (existing): use `StdioServerParameters` + `stdio_client` (unchanged).
- Both paths produce a `ClientSession` — downstream code is transport-agnostic.

URL and command are mutually exclusive per server (validated in settings).

### Decision 5 — `check_mcp_transport.py` updated, not removed

The CI gate is refactored, not deleted:

- SSE remains permanently forbidden (deprecated in MCP spec, known session-management weaknesses).
- Streamable HTTP imports are allowed in an explicit allowlist of file patterns:
  - `mcp-servers/*/src/*/__main__.py`
  - `mcp-servers/*/src/*/auth/*.py`
  - `packages/mcp_auth/` (the shared middleware)
  - `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py`
  - `services/orchestrator-adapter/src/orchestrator_adapter/adapters/mcp_clients.py`
- Self-test fixtures are updated with clean/violation cases for the new rules.
- The gate's regression-prevention intent is preserved: a developer cannot accidentally introduce streamable-http in a package or service outside these controlled locations.

### Decision 6 — Phase 2 future: OAuth 2.1 with PKCE

Recorded as a future ADR. The bearer token scheme is intentionally simple for single-operator Docker deployments. OAuth 2.1 is the correct model for multi-tenant or external-tool access, but requires an authorization server, client registration, and redirect flows — all out of scope for Phase 10.

## Consequences

### Positive

- **Split deployment unlocked.** Workers can run on separate hosts, connecting to MCP servers over HTTP. This is the architecture document's top-priority future work item.
- **Backward compatible.** Every existing deployment continues to work unchanged — stdio is the default, and all new code paths are gated on `MCP_TRANSPORT=streamable-http` being set.
- **No new external dependencies.** The `mcp` SDK already has Streamable HTTP support; `pyjwt` is already used by registry-api; `httpx` is already installed.
- **Security reuse.** The same `JWT_SECRET_KEY` secures both registry-api HTTP and MCP Streamable HTTP endpoints.
- **CI gate preserved.** The `check_mcp_transport.py` gate continues to prevent accidental non-stdio transport imports, now with a controlled allowlist.

### Negative

- **Increased attack surface.** MCP servers listening on HTTP (even Docker-internal only) are more reachable than stdio subprocesses. Mitigated by bearer token auth and no external port exposure.
- **`JWT_SECRET_KEY` exposure expands.** The key is now also forwarded to MCP server containers (not just registry-api). Mitigated by per-server env isolation (G-SEC-2 defense-in-depth) ensuring it only reaches MCP servers, never Claude/Codex/Gemini child processes.
- **Code complexity in `mcp_clients.py`.** The `_connect()` method branches on transport mode. Both spawners must be kept in sync (mirror discipline).
- **Token replay within Docker network.** Bearer tokens are valid for their full lifetime (default 24h). Mitigated by Docker network isolation; OAuth 2.1 with shorter-lived tokens addresses this in Phase 2.

## Alternatives considered

- **Full OAuth 2.1 from the start.** Rejected — adds significant complexity (authorization server, client registration, redirect flows) that is not needed for single-operator Docker deployments. Bearer token from shared secret is sufficient and consistent with the existing JWT auth pattern.
- **SSE transport.** Rejected — deprecated in the MCP specification (2024-11-05 version), replaced by Streamable HTTP. SSE has known session-management weaknesses. Permanently forbidden by the CI gate.
- **Shared `mcp-server-kit` package for transport code.** Rejected for Phase 10 — the import-graph constraint (Story 5.8) blocks mcp-servers from sharing code directly. The `packages/mcp_auth/` approach works because mcp-servers may import from `packages/` (ADR-0010 allows this). A future `packages/mcp-kit` consolidation is a legitimate tech-debt item.
- **Separate Docker images per MCP server.** Rejected — contradicts ADR-0010 Decision 7 (ships in base image). The same binary supports both transports; no new Dockerfile or compose service is needed for stdio mode. Remote mode uses an optional compose profile, not a new image.
- **Ambient `trace_id` via HTTP header without auth.** Rejected — without authentication, any container on the Docker network could forge requests. Bearer token auth is mandatory for streamable-http.

## Linked artifacts

- ADR-0010 — MCP-server-authoring pattern (Decision 1 amended by this ADR).
- ADR-0009 — Phase-3 gate (the context that established P2-I4).
- `docs/architecture.md:246-257` — "Future work beyond Phase 9" with Remote MCP transport as #1.
- `scripts/check_mcp_transport.py` — CI gate enforcing P2-I4 (updated by this ADR).
- `services/registry-api/src/registry_api/adapters/middleware.py` — JWT auth pattern reused for MCP bearer tokens.
- MCP Specification (2025-03-26) — [Transports](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports), [Authorization](https://modelcontextprotocol.io/specification/2025-03-26/basic/authorization).

— *R2d2, 2026-06-09.*
