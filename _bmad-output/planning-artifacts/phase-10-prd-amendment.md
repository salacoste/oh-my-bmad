# Phase 10 Scope Extension — Remote MCP Transport (Streamable HTTP)

> **Status:** Phase-10 PRD amendment. Opens Streamable HTTP transport as opt-in alternative to stdio, with JWT bearer token auth. FR/NFR numbering continues the canonical series (FR122 → FR126; NFR-S13 → S14; NFR-M10; NFR-O19; NFR-R15). Epic numbering starts at 50.
>
> **Selected via:** ADR-0022 accepted 2026-06-09. Architecture document's "Future work beyond Phase 9" listed Remote MCP transport as #1 priority.

**Theme:** the **remote MCP transport** — Streamable HTTP with JWT bearer token auth, unlocking split deployment and remote workers.

**Resolved scope (from Phase 9 retrospective + ADR-0022):**

- **IN.** Streamable HTTP transport mode for MCP servers (opt-in via MCP_TRANSPORT=streamable-http)
- **IN.** JWT bearer token auth middleware (reusing JWT_SECRET_KEY from registry-api)
- **IN.** Client-side dual transport (streamable_http_client alongside stdio_client)
- **IN.** CI gate update (check_mcp_transport.py refactored to allowlist streamable-http)
- **IN.** Extended separability tests for dual-transport mode
- **IN.** Docker compose profile for remote transport
- **OUT.** OAuth 2.1 (future ADR), SSE transport (permanently forbidden), new services, new MCP servers

**Preserved invariants (carry from Phases 1–9 — non-negotiable):**

- **All prior invariants stand unchanged (P1-I1 through P9-I1).** Phase 10 extends transport; it does not alter existing invariants.
- **Single-writer (FR26) unchanged.** No new writers.
- **Event-only telemetry unchanged.** No new instrumentation paths.
- **Tier-enforced authz unchanged.** No new tier definitions.
- **Runtime adapter protocol unchanged.** No adapter changes.
- **SSE transport PERMANENTLY FORBIDDEN.** No SSE imports, no SSE server transports, no exceptions.

---

## Phase 10 Functional Requirements

### Alpha — Auth middleware (Epic 50)

- **FR122.** Streamable HTTP transport mode. Each MCP server's `__main__.py` gains a `_resolve_transport()` helper. When `MCP_TRANSPORT=streamable-http`: call `mcp.run(transport="streamable-http", host="0.0.0.0", port=<MCP_PORT>)` with bearer token auth middleware. When absent or `"stdio"` (default): existing `mcp.run()` — zero change. The server binary is the same; only the runtime transport changes.

  **Acceptance criteria:**
  - `_resolve_transport()` helper reads `MCP_TRANSPORT` env var
  - Default (absent/var="stdio") = stdio transport, zero behavior change
  - `MCP_TRANSPORT=streamable-http` activates HTTP transport on configured port
  - `MCP_PORT` env var controls listen port (default per-server)
  - Auth middleware mounted when streamable-http active
  - Server binary is identical for both modes (ADR-0010 Decision 7 preserved)

- **FR123.** JWT bearer token auth middleware. New `packages/mcp_auth/` workspace package provides ASGI middleware validating `Authorization: Bearer <token>` on every Streamable HTTP request. Reuses existing `JWT_SECRET_KEY` + HS256 algorithm. Extracts `actor_id` from `sub` claim. Returns HTTP 401 on missing/invalid/expired tokens. When `JWT_SECRET_KEY` not set, middleware not installed (stdio mode never sees auth).

  **Acceptance criteria:**
  - `BearerTokenMiddleware` validates JWT tokens on ASGI requests
  - Extracts `actor_id` from `sub` claim, injects `X-Actor-Id` header
  - Returns 401 JSON for missing/invalid/expired tokens
  - `McpAuthSettings.enabled` returns False when no JWT_SECRET_KEY configured
  - `GET /healthz` bypasses auth (health probe)
  - Uses PyJWT (already in deps) + Pydantic (already in deps)
  - No new external dependencies

### Beta — CI gate refactor (Epic 51)

- **FR124.** Client-side dual transport. `MCPClientGroup._connect()` in `mcp_clients.py` gains transport-mode branch. When `<SERVER>_URL` env var set: use `streamable_http_client(url, headers={"Authorization": f"Bearer {token}"})`. When `<SERVER>_COMMAND` set (existing): use `StdioServerParameters` + `stdio_client`. Both paths produce `ClientSession` — downstream code is transport-agnostic. URL and command are mutually exclusive per server.

  **Acceptance criteria:**
  - `_connect()` branches on transport mode
  - URL-based connection uses streamable_http_client with bearer token
  - Command-based connection uses existing stdio path (unchanged)
  - Both produce `ClientSession` — no downstream changes
  - URL and command are mutually exclusive (validated in settings)
  - Dual spawners (worker-wrapper + orchestrator-adapter) kept in sync

### Gamma — Dual-mode server __main__.py (Epic 52)

- **FR125.** CI gate update. `check_mcp_transport.py` refactored (not removed): SSE permanently forbidden, Streamable HTTP imports allowed in explicit allowlist of file patterns (server __main__.py, auth modules, mcp_clients.py). Self-test fixtures updated. Gate's regression-prevention intent preserved.

  **Acceptance criteria:**
  - SSE imports (`mcp.server.sse`, `SseServerTransport`, `sse_app`) unconditionally forbidden
  - Streamable HTTP imports allowed in designated files only
  - Allowlist: `mcp-servers/*/src/*/__main__.py`, `mcp-servers/*/src/*/auth/*.py`, `packages/mcp_auth/**`, `*/mcp_clients.py`
  - Self-test passes with updated clean/violation fixtures
  - Main scan passes (0 violations across 260+ files)

### Delta — Client-side dual transport (Epic 53)

- **FR126.** Extended separability tests + Docker compose profile. Separability test suite extended with dual-transport scenarios (stdio-only compose, streamable-http compose profile). Docker compose profile `remote-mcp` mounts MCP servers with `MCP_TRANSPORT=streamable-http` + auth env vars. No external ports exposed (NFR-S13).

  **Acceptance criteria:**
  - `docker compose --profile remote-mcp up` starts servers in streamable-http mode
  - No external ports exposed (Docker internal network only)
  - Separability test for dual-transport mode passes
  - Default compose (no profile) = stdio-only (zero change, NFR-M10)
  - Health probes work in both modes

## Phase 10 Non-Functional Requirements

- **NFR-S13 (No external ports).** MCP servers listening on Streamable HTTP bind to Docker internal network only. No port mapping to the host. Verified by compose config audit.
- **NFR-S14 (Token validation latency).** Bearer token validation completes in <5ms p99 (PyJWT HS256 verify). No database lookup in auth path.
- **NFR-M10 (Zero-change backward compatibility).** Default deployment (no MCP_TRANSPORT env var) is IDENTICAL to Phase 9 behavior. Every new code path is gated on `MCP_TRANSPORT=streamable-http` or `<SERVER>_URL` being set. CI gates pass without any configuration changes.
- **NFR-O19 (Transport mode observable).** Transport mode (stdio/streamable-http) is logged at server startup and emitted as a structured field in server health probes.
- **NFR-R15 (Transport fallback).** Client-side connection fails fast with clear error message when streamable-http server is unreachable. No silent fallback to stdio (would mask misconfiguration).

## Phase 10 Invariants

- **P10-I1: Stdio-by-default, streamable-http opt-in.** SSE permanently forbidden. Streamable HTTP permitted only via explicit MCP_TRANSPORT=streamable-http env var.
- **P10-I2: Remote MCP auth required.** Any MCP server on Streamable HTTP MUST validate bearer tokens. Unauthenticated Streamable HTTP forbidden. Docker network isolation + bearer token = defense-in-depth.

## Phase 10 Architecture Decisions Required

- **ADR-0022: Remote MCP transport** — accepted 2026-06-09
- **ADR-0023: Phase 10 gate** — to be created after epics decomposed

## Phase 10 Ship-Blocker Checklist

1. [ ] All Phase 1–9 invariants regression-free
2. [ ] ADR-0022 accepted
3. [ ] `check_mcp_transport.py --self-test` passes
4. [ ] `check_mcp_transport.py --verbose` passes (0 violations)
5. [ ] `packages/mcp_auth/` imports cleanly, McpAuthSettings.from_env() works
6. [ ] Default deployment (no env vars) = identical to Phase 9
7. [ ] `just lint` EXIT 0
8. [ ] All discipline scripts exit 0
9. [ ] No new third-party dependencies (PyJWT + Pydantic already in deps)
10. [ ] Event cardinality updated for any new event types
11. [ ] Both spawners (worker-wrapper + orchestrator-adapter) updated for dual transport
12. [ ] Docker compose profile `remote-mcp` verified
13. [ ] Separability tests pass for both transport modes
14. [ ] Phase 10 retrospective produced

## Estimated Effort

**6 epics, ~15 stories, ~3-4 weeks solo-operator work.**

| Epic | Stories | Estimate |
|------|---------|----------|
| 50 — Auth middleware | 2 | ~3 days |
| 51 — CI gate refactor | 2 | ~2 days |
| 52 — Dual-mode server __main__.py | 3 | ~4 days |
| 53 — Client-side dual transport | 3 | ~5 days |
| 54 — Docker compose profile | 2 | ~2 days |
| 55 — Separability + validation | 3 | ~4 days |

Note: Epics 50-51 are already partially shipped (mcp_auth package exists, CI gate already updated). Epics 52-55 are the remaining implementation work.

-- *Amendment by R2d2, 2026-06-09, via the BMad planning workflow (Phase 10 scoping).*
