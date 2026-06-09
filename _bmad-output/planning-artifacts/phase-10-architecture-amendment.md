## Phase 10 Architecture Amendment -- Remote MCP Transport (Streamable HTTP)

> **Amendment added:** 2026-06-09.
>
> **Companion documents:**
> - PRD amendment: see [`phase-10-prd-amendment.md`](./phase-10-prd-amendment.md) (FR122-FR126).
> - Remote MCP transport ADR: see [`docs/adr/0022-remote-mcp-transport.md`](../../docs/adr/0022-remote-mcp-transport.md).
> - Prior amendments: [`phase-8-architecture-amendment.md`](./phase-8-architecture-amendment.md) (P8-I2), [`phase-6-architecture-amendment.md`](./phase-6-architecture-amendment.md) (P6-I1 through P6-I5).

**Theme.** Remote MCP transport -- Streamable HTTP with JWT bearer token auth, unlocking split deployment and remote workers. Phase 10 amends the stdio-only transport invariant (P2-I4) to permit opt-in Streamable HTTP, adds bearer token authentication for remote MCP servers, and introduces dual-mode client branching. Default deployments remain identical to Phase 9.

### Preserved invariants (Phase 1 through Phase 9 carry forward)

All prior invariants stand unchanged. As they apply to the new surface:

- **FR26 single-writer (P2-I1).** Transport change does not affect persistence. Each MCP server writes to the same stores regardless of transport mode.
- **Event-driven state transitions (P6-I3).** Transport events emitted through the normal event spine. No direct state mutations.
- **Credential isolation (P5-I1, P6-I5).** JWT_SECRET_KEY shared with registry-api but per-server env scoping ensures it only reaches MCP servers. No server gains access to another server's credentials.
- **Tier-enforced authz.** Transport does not change tier definitions. Authorization boundaries are unaffected.
- **Runtime adapter protocol (ADR-0015).** No changes. Transport is orthogonal to the adapter protocol.
- **Browser session ephemerality (P4-I1).** Unaffected. Remote transport does not introduce browser-persistent state.

### New invariants (Phase 10)

| # | Invariant | Why |
|---|-----------|-----|
| **P10-I1** | **Stdio-by-default, streamable-http opt-in (amends P2-I4).** SSE permanently forbidden. Streamable HTTP permitted only via explicit `MCP_TRANSPORT=streamable-http`. | ADR-0022 Decision 1. Preserves zero-change backward compatibility while enabling remote transport. |
| **P10-I2** | **Remote MCP auth required.** Any MCP server on Streamable HTTP MUST validate bearer tokens. Unauthenticated Streamable HTTP forbidden. | Defense-in-depth: Docker network isolation + bearer token. Single-operator model tolerates symmetric-key bearer scheme. |

### ADR-0022: Remote MCP Transport

**Location:** `docs/adr/0022-remote-mcp-transport.md`

**Decision 1 -- Amend P2-I4:** stdio-by-default, streamable-http opt-in. `check_mcp_transport.py` enforces allowlist pattern. SSE permanently forbidden.

**Decision 2 -- Dual-mode `__main__.py`:** Same server binary, runtime transport switch via `_resolve_transport()`. When streamable-http: `mcp.run(transport="streamable-http", host="0.0.0.0", port=<MCP_PORT>)` with auth middleware. When stdio: existing `mcp.run()`.

**Decision 3 -- Bearer token auth from JWT_SECRET_KEY:** `packages/mcp_auth/` provides raw ASGI BearerTokenMiddleware. HS256 symmetric, reuses registry-api key material. NOT OAuth 2.1 (tracked as Decision 6 future work).

**Decision 4 -- Client-side streamable_http_client:** `MCPClientGroup._connect()` branches on URL vs command. Both produce ClientSession. URL and command mutually exclusive.

**Decision 5 -- CI gate updated, not removed:** SSE permanently forbidden. Streamable HTTP allowed in allowlisted files only. Self-test updated.

**Decision 6 -- Future: OAuth 2.1 with PKCE:** Recorded for future ADR. Bearer token is sufficient for single-operator Docker.

### Auth middleware architecture (packages/mcp_auth/)

```
┌─────────────────────────────────────────────────────┐
│  MCP Server (streamable-http mode)                  │
│                                                     │
│  ASGI stack:                                        │
│  ┌─────────────────────────────────────────────┐   │
│  │ BearerTokenMiddleware (packages/mcp_auth/)   │   │
│  │ - validates JWT from Authorization header     │   │
│  │ - injects X-Actor-Id into ASGI scope          │   │
│  │ - returns 401 JSON on failure                  │   │
│  │ - skips GET /healthz (health probe)            │   │
│  │ - passthrough when JWT_SECRET_KEY absent       │   │
│  └─────────────────────────────────────────────┘   │
│                     │                               │
│  ┌─────────────────▼───────────────────────────┐   │
│  │ FastMCP (mcp SDK)                            │   │
│  │ - handles MCP protocol over HTTP              │   │
│  │ - reads X-Actor-Id from scope for audit       │   │
│  └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### Dual-mode __main__.py pattern

```python
# mcp-servers/<server>/src/<pkg>/__main__.py
def _resolve_transport() -> str:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport not in ("stdio", "streamable-http"):
        raise ValueError(f"Unknown MCP transport: {transport!r}")
    return transport

def main() -> None:
    transport = _resolve_transport()
    if transport == "streamable-http":
        from mcp_auth import BearerTokenMiddleware, McpAuthSettings
        settings = McpAuthSettings.from_env()
        auth_middleware = BearerTokenMiddleware  # noqa: MCP001 -- allowed in __main__.py
        mcp.run(transport="streamable-http", host="0.0.0.0", port=int(os.environ.get("MCP_PORT", "8080")))
    else:
        mcp.run()
```

### Client-side dual transport pattern

```python
# mcp_clients.py -- dual transport branch
async def _connect(self, server_name: str) -> ClientSession:
    url = getattr(settings, f"{server_name}_url".upper(), None)
    command = getattr(settings, f"{server_name}_command".upper(), None)

    if url:
        # Streamable HTTP -- remote mode
        token = self._get_auth_token()
        client_transport = await streamable_http_client(url, headers={"Authorization": f"Bearer {token}"})  # noqa: MCP001
    elif command:
        # Stdio -- local mode (unchanged)
        params = StdioServerParameters(command=command, ...)
        client_transport = await stdio_client(params)
    else:
        raise ValueError(f"No URL or command for {server_name}")

    session = ClientSession(client_transport)
    await session.initialize()
    return session
```

### Docker compose profile

```yaml
# docker-compose.yml -- remote-mcp profile
profiles:
  remote-mcp:
    services:
      task-registry-mcp:
        environment:
          MCP_TRANSPORT: streamable-http
          MCP_PORT: "8081"
          JWT_SECRET_KEY: ${JWT_SECRET_KEY}
        networks:
          - internal
        # NO ports: mapping -- internal only (NFR-S13)
```

### Per-epic wiring decisions

**Epic 50 -- Auth middleware.** `packages/mcp_auth/` (ALREADY SHIPPED). BearerTokenMiddleware + McpAuthSettings. PyJWT + Pydantic only.

**Epic 51 -- CI gate refactor.** `scripts/check_mcp_transport.py` (ALREADY SHIPPED). SSE permanently forbidden, streamable-http allowlisted. Self-test fixtures updated.

**Epic 52 -- Dual-mode server __main__.py.** Each of the 9 MCP servers gains `_resolve_transport()`. Auth middleware mounted when streamable-http. Zero-change for stdio.

**Epic 53 -- Client-side dual transport.** Both `mcp_clients.py` files gain URL/command branching. Mutual exclusion validated. Bearer token from `JWT_SECRET_KEY` or `MCP_AUTH_TOKEN`.

**Epic 54 -- Docker compose profile.** `docker-compose.yml` gains `remote-mcp` profile. No external ports. JWT_SECRET_KEY forwarded to MCP servers only.

**Epic 55 -- Separability + validation.** Extended separability tests. Full CI validation. Phase 10 retrospective.

### Phase 10 CI-gate additions

The PR-required-checks list expands per epic:

- **Epic 50:** `packages/mcp_auth/` imports cleanly. BearerTokenMiddleware validates HS256 JWT. Returns 401 JSON on failure. Passthrough when JWT_SECRET_KEY absent.
- **Epic 51:** `check_mcp_transport.py --self-test` passes (6 fixtures). `check_mcp_transport.py --verbose` passes (0 violations, 260+ files).
- **Epic 52:** Each MCP server starts in stdio mode (default). Each MCP server starts in streamable-http mode when MCP_TRANSPORT set. Zero-change for stdio confirmed.
- **Epic 53:** Client connects via stdio (command). Client connects via streamable-http (URL). Mutual exclusion validated (URL + command raises error).
- **Epic 54:** Default compose = Phase 9 identical behavior. `remote-mcp` profile starts without external ports. JWT_SECRET_KEY forwarded to MCP servers only.
- **Epic 55:** Extended separability tests pass. Full CI validation green. Phase 10 retrospective produced.

### Acceptance checklist

- [ ] Architecture amendment (this section) accepted; P10-I1 and P10-I2 invariants explicitly stated.
- [ ] ADR-0022 (`docs/adr/0022-remote-mcp-transport.md`) accepted.
- [ ] PRD amendment (FR122-FR126) accepted.
- [ ] `bmad-create-epics-and-stories` has decomposed the scope into Epics 50-55 stories.
- [ ] Each Phase 10 epic has its `phase: 10` label set in `sprint-status.yaml`.
- [ ] Default deployment identical to Phase 9 (NFR-M10).
- [ ] Phase 10 retrospective produced.

-- *Amendment by R2d2, 2026-06-09, via the BMad bmad-create-architecture workflow (amendment mode).*
