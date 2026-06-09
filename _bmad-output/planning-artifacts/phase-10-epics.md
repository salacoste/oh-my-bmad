---
stepsCompleted:
  - step-01-validate-prerequisites
  - step-02-design-epics
  - step-03-create-stories
  - step-04-final-validation
workflowStatus: complete
finalStoryCount: 15
finalEpicCount: 6
inputDocuments:
  - _bmad-output/planning-artifacts/phase-10-prd-amendment.md
  - _bmad-output/planning-artifacts/phase-10-architecture-amendment.md
  - docs/adr/0022-remote-mcp-transport.md
workflowType: epics-and-stories
project_name: oh-my-bmad
user_name: R2d2
date: '2026-06-09'
---

# oh-my-bmad -- Phase 10 Epic Breakdown: Remote MCP Transport (Streamable HTTP)

## Overview

Phase 10 opens **Streamable HTTP transport** as an opt-in alternative to stdio, with JWT bearer token auth. This document decomposes FR122-FR126 and associated NFRs into **6 epics (50-55) and 15 stories**. Note: Epics 50-51 are already partially/fully shipped (auth middleware package and CI gate exist).

Source documents:
- PRD amendment: `_bmad-output/planning-artifacts/phase-10-prd-amendment.md` (FR122-FR126)
- Architecture amendment: `_bmad-output/planning-artifacts/phase-10-architecture-amendment.md`
- ADR-0022: `docs/adr/0022-remote-mcp-transport.md`

## Requirements Inventory

### Functional Requirements

**FR122.** Streamable HTTP transport mode: dual-mode `__main__.py` supporting both stdio (default) and streamable-http (opt-in via `MCP_TRANSPORT` env var).

**FR123.** JWT bearer token auth middleware: `packages/mcp_auth/` with `BearerTokenMiddleware` (raw ASGI), `McpAuthSettings` (JWT HS256), and `McpAuthSettings.from_env()`.

**FR124.** Client-side dual transport: `mcp_clients.py` branches on `<SERVER>_URL` (streamable-http) vs `<SERVER>_COMMAND` (stdio).

**FR125.** CI gate update: `check_mcp_transport.py` enforces SSE permanently forbidden, streamable-http allowed in allowlisted files only, AST-based detection.

**FR126.** Extended separability tests + Docker compose profile: remote-mcp profile with internal networking, no external ports.

### Non-Functional Requirements

**NFR-S13.** No external ports: Docker compose remote-mcp profile uses internal network only, no `ports:` mappings to host.

**NFR-S14.** Token validation <5ms: HS256 verify with no DB lookup, measured at the auth middleware.

**NFR-M10.** Zero-change backward compatibility: default deployment (no `MCP_TRANSPORT` env var) is identical to Phase 9 behavior.

**NFR-O19.** Transport mode observable: transport mode logged at startup, health probes report transport type.

**NFR-R15.** Transport fallback: clear error messages when transport mode unavailable; no silent fallback to stdio.

### FR Coverage Map

| FR | Epic | Story IDs | Notes |
|----|------|-----------|-------|
| FR122 | 52 | 52.1, 52.2, 52.3 | Dual-mode server |
| FR123 | 50 | 50.1, 50.2 | Auth middleware |
| FR124 | 53 | 53.1, 53.2, 53.3 | Client-side transport |
| FR125 | 51 | 51.1, 51.2 | CI gate refactor |
| FR126 | 54, 55 | 54.1, 54.2, 55.1, 55.2 | Compose profile + separability |

**100% FR coverage confirmed -- 5 FRs mapped across 6 epics, zero orphans.**

### NFR Coverage Map

| NFR | Epic | Story IDs | Notes |
|-----|------|-----------|-------|
| NFR-S13 | 54 | 54.1, 54.2 | Internal network only |
| NFR-S14 | 50 | 50.1 | HS256, no DB lookup |
| NFR-M10 | 55 | 55.2 | Default = Phase 9 identical |
| NFR-O19 | 52 | 52.1 | Startup logging |
| NFR-R15 | 53 | 53.1, 53.3 | Clear error, no silent fallback |

**100% NFR coverage confirmed -- 5 NFRs mapped across 4 epics, zero orphans.**

## Epic List

### Dependency Graph

```
Epic 50 (Auth Middleware) ────┐
Epic 51 (CI Gate) ────────────┤──► Epic 52 (Dual-Mode Server)
                              │         │
                              │         ▼
                              │    Epic 53 (Client Transport)
                              │         │
                              │         ▼
                              └──► Epic 54 (Docker Compose Profile)
                                        │
                                        ▼
                                  Epic 55 (Separability + Validation)
```

### Standalone Value

- **Epic 50** delivers: JWT auth middleware package (`packages/mcp_auth/`) with BearerTokenMiddleware and McpAuthSettings. **Status: SHIPPED.**
- **Epic 51** delivers: CI gate enforcing SSE forbidden, streamable-http allowlisted. **Status: SHIPPED.**
- **Epic 52** delivers: All 9 MCP servers gain dual-mode transport (stdio default, streamable-http opt-in) with auth middleware integration.
- **Epic 53** delivers: Client-side transport branching in both spawners, URL-based streamable-http with Bearer token auth.
- **Epic 54** delivers: Docker compose `remote-mcp` profile with internal networking, no external ports.
- **Epic 55** delivers: Extended separability tests, full CI validation, and Phase 10 retrospective.

### Sequencing Rationale

Epics 50 and 51 are already shipped. Epic 52 depends on 50 (auth middleware) and 51 (must pass CI gate). Epic 53 depends on 52 (client needs server to connect to). Epic 54 depends on 52+53 (compose needs both sides). Epic 55 lands last as the definitive validation gate.

## Epic 50: Auth Middleware Package (backlog)

**Goal.** Provide the JWT bearer token auth middleware used by streamable-http transport. The `packages/mcp_auth/` package contains `BearerTokenMiddleware` (raw ASGI), `McpAuthSettings` (JWT HS256 configuration), and `McpAuthSettings.from_env()`. Token validation completes in <5ms via HS256 verify with no DB lookup (NFR-S14).

**FRs covered:** FR123
**NFRs:** NFR-S14
**Status:** SHIPPED

### Story 50.1: Auth Middleware Implementation

**Title:** Create packages/mcp_auth/ with BearerTokenMiddleware

**Description:** Create `packages/mcp_auth/` with BearerTokenMiddleware (raw ASGI), McpAuthSettings (JWT HS256), McpAuthSettings.from_env(). Validate JWT tokens, inject X-Actor-Id, return 401 JSON, skip /healthz.

**Acceptance criteria:**
1. BearerTokenMiddleware validates JWT tokens.
2. Extracts actor_id from `sub` claim.
3. Returns 401 JSON on invalid/missing token.
4. McpAuthSettings.enabled is False when no `JWT_SECRET_KEY` set.
5. GET /healthz bypasses auth.
6. Dependencies: PyJWT + Pydantic only.
7. NFR-S14: Token validation <5ms (HS256 verify, no DB lookup).

**Size:** M
**FR/NFR reference:** FR123, NFR-S14
**ATDD contracts:**
- Given a valid JWT with `sub: "actor-123"`, when the middleware processes the request, then `X-Actor-Id: actor-123` is injected and the request passes through.
- Given an expired JWT, when the middleware processes the request, then a 401 JSON response is returned.
- Given a request to GET /healthz, when no Authorization header is present, then the request passes through (auth bypassed).
- Given no `JWT_SECRET_KEY` env var, when McpAuthSettings.from_env() is called, then `enabled` is False.
**Status:** DONE

### Story 50.2: Auth Middleware Tests

**Title:** Unit tests for BearerTokenMiddleware and McpAuthSettings

**Description:** Unit tests for BearerTokenMiddleware: valid token, expired token, invalid signature, missing header, malformed header, empty token, health probe bypass, disabled mode. Test McpAuthSettings validation (min 32 bytes, empty to None).

**Acceptance criteria:**
1. >=10 unit tests covering all auth paths.
2. McpAuthSettings edge cases covered (None, empty, short, valid).
3. X-Actor-Id header injection tested.
4. Scope state tested (actor_id, authenticated).

**Size:** S
**FR/NFR reference:** FR123
**ATDD contracts:**
- Given a valid JWT, when processed, then actor_id is correctly extracted and state is set.
- Given an invalid signature JWT, when processed, then 401 is returned and state is not set.
- Given McpAuthSettings with a 16-byte secret, when validated, then an error is raised (min 32 bytes).
- Given McpAuthSettings with an empty secret, when validated, then `enabled` resolves to False/None.
**Status:** TODO

---

## Epic 51: CI Gate Refactor (backlog)

**Goal.** Update `check_mcp_transport.py` to enforce SSE permanently forbidden and streamable-http allowed only in allowlisted files (AST-based detection, not grep). Include self-test fixtures.

**FRs covered:** FR125
**Status:** SHIPPED

### Story 51.1: CI Gate Refactor

**Title:** Update check_mcp_transport.py for streamable-http allowlisting

**Description:** Update check_mcp_transport.py: SSE permanently forbidden, streamable-http allowed in allowlisted files (`mcp-servers/*/src/*/__main__.py`, `mcp-servers/*/src/*/auth/*.py`, `packages/mcp_auth/**`, `*/mcp_clients.py`). AST-based detection (not grep). Self-test fixtures.

**Acceptance criteria:**
1. SSE unconditionally forbidden.
2. Streamable-http allowlisted by file path pattern.
3. Self-test passes (6 fixtures).
4. Main scan passes (0 violations, 260+ files).

**Size:** M
**FR/NFR reference:** FR125
**ATDD contracts:**
- Given an SSE import in any file, when the check runs, then it reports a violation.
- Given a streamable-http import in `mcp-servers/task-registry/src/task_registry/__main__.py`, when the check runs, then no violation is reported.
- Given a streamable-http import in `services/worker-wrapper/src/worker_wrapper/spawner.py`, when the check runs, then a violation is reported (not in allowlist).
**Status:** DONE

### Story 51.2: Self-Test Fixture for Allowed Paths

**Title:** Create fixture demonstrating streamable-http imports in designated files

**Description:** Create fixture file demonstrating streamable-http imports in designated files are allowed. Verify `_is_streamable_http_allowed_file` matches correct patterns.

**Acceptance criteria:**
1. Fixture in `scripts/checks/fixtures/mcp_transport/clean/`.
2. Contains forbidden AST nodes.
3. Zero violations in self-test.

**Size:** S
**FR/NFR reference:** FR125
**ATDD contracts:**
- Given the fixture file with streamable-http imports at an allowed path, when the self-test runs, then zero violations are reported.
- Given the fixture file with SSE imports, when the self-test runs, then a violation is reported.
**Status:** DONE

---

## Epic 52: Dual-Mode Server __main__.py (backlog)

**Goal.** All 9 MCP servers gain dual-mode transport: stdio (default, zero-change) and streamable-http (opt-in via `MCP_TRANSPORT` env var). When streamable-http is active, BearerTokenMiddleware wraps the ASGI app. Transport mode is logged at startup (NFR-O19). A single server binary handles both modes.

**FRs covered:** FR122
**NFRs:** NFR-O19

### Story 52.1: _resolve_transport Helper

**Title:** Add dual-transport mode to all 9 MCP server __main__.py files

**Description:** Create a shared `_resolve_transport()` helper that reads `MCP_TRANSPORT` env var, validates it ("stdio" or "streamable-http"), raises ValueError for unknown values. Default = "stdio". Add to each of the 9 MCP servers' `__main__.py`. When streamable-http: import BearerTokenMiddleware + McpAuthSettings from mcp_auth, wrap the ASGI app, call `mcp.run(transport="streamable-http", host="0.0.0.0", port=int(MCP_PORT))`. When stdio: existing `mcp.run()` unchanged.

**Acceptance criteria:**
1. All 9 MCP servers have `_resolve_transport()`.
2. Default (no MCP_TRANSPORT) = stdio, zero change.
3. MCP_TRANSPORT=streamable-http activates HTTP mode.
4. BearerTokenMiddleware mounted in HTTP mode.
5. Transport mode logged at startup (NFR-O19).
6. Server binary identical for both modes.
7. `check_mcp_transport.py` still passes (imports in __main__.py are allowlisted).

**Size:** M
**FR/NFR reference:** FR122, NFR-O19
**ATDD contracts:**
- Given no `MCP_TRANSPORT` env var, when a server starts, then it runs in stdio mode (identical to Phase 9).
- Given `MCP_TRANSPORT=streamable-http`, when a server starts, then BearerTokenMiddleware wraps the ASGI app and the server binds to the configured port.
- Given `MCP_TRANSPORT=invalid`, when a server starts, then ValueError is raised.
- Given startup in either mode, when the log is inspected, then the transport mode is logged.

### Story 52.2: Per-Server Port Configuration

**Title:** Add MCP_PORT env var with per-server defaults

**Description:** Add `MCP_PORT` env var to each MCP server's settings (default port per server: task-registry=8081, session-registry=8082, git=8083, github=8084, verification=8085, memory=8086, artifact=8087, browser=8088, clawhip-bridge=8089). Document defaults in ADR-0022 or operator runbook.

**Acceptance criteria:**
1. Each server has a default MCP_PORT.
2. MCP_PORT env var overrides default.
3. Port documented in operator runbook.
4. Ports do not conflict with existing services (registry-api=8000, metrics=8001, etc.).

**Size:** S
**FR/NFR reference:** FR122
**ATDD contracts:**
- Given no `MCP_PORT` env var, when task-registry starts in HTTP mode, then it binds to port 8081.
- Given `MCP_PORT=9090`, when a server starts in HTTP mode, then it binds to port 9090.
- Given the port list, when compared to existing service ports, then no conflicts exist.

### Story 52.3: Server Startup Integration Tests

**Title:** Integration tests for both transport modes on representative servers

**Description:** Integration tests verifying both transport modes for at least 2 representative servers (task-registry, git): stdio mode works unchanged, streamable-http mode starts and responds to health probe, auth middleware rejects unauthenticated requests, auth middleware accepts valid JWT tokens.

**Acceptance criteria:**
1. Integration test for stdio mode (no env vars set).
2. Integration test for streamable-http mode (`MCP_TRANSPORT=streamable-http`).
3. Test auth rejection (no token -> 401).
4. Test auth acceptance (valid token -> 200).
5. Test healthz bypass (no auth needed for GET /healthz).

**Size:** M
**FR/NFR reference:** FR122
**ATDD contracts:**
- Given task-registry in stdio mode, when the server starts, then it responds to tool calls over stdio.
- Given task-registry in streamable-http mode, when an unauthenticated request is sent, then 401 is returned.
- Given task-registry in streamable-http mode with a valid JWT, when an authenticated request is sent, then 200 is returned.
- Given task-registry in streamable-http mode, when GET /healthz is sent without auth, then 200 is returned.

---

## Epic 53: Client-Side Dual Transport (backlog)

**Goal.** Both MCP spawners (worker-wrapper and orchestrator-adapter) gain transport-mode branching in `_connect()`. When `<SERVER>_URL` is set, use streamable_http_client with Bearer token. When `<SERVER>_COMMAND` is set, use existing stdio path. URL and command are mutually exclusive. When no token is available in URL mode, a clear error is raised (NFR-R15: no silent fallback).

**FRs covered:** FR124
**NFRs:** NFR-R15

### Story 53.1: _connect Transport Branching

**Title:** Add transport-mode branch to MCPClientGroup._connect()

**Description:** Add transport-mode branch to `MCPClientGroup._connect()` in both `services/worker-wrapper/.../mcp_clients.py` and `services/orchestrator-adapter/.../mcp_clients.py`. When `<SERVER>_URL` set: use streamable_http_client with Bearer token. When `<SERVER>_COMMAND` set: existing stdio path. Both produce ClientSession. Mutual exclusion validated.

**Acceptance criteria:**
1. `_connect()` branches on URL vs command.
2. URL path uses streamable_http_client with Authorization header.
3. Command path unchanged (stdio).
4. Both paths produce ClientSession.
5. URL and command mutually exclusive (ValueError if both set).
6. Both spawners updated in sync.
7. check_mcp_transport.py passes (imports in mcp_clients.py are allowlisted).

**Size:** M
**FR/NFR reference:** FR124, NFR-R15
**ATDD contracts:**
- Given `TASK_REGISTRY_URL=http://task-registry:8081`, when `_connect()` runs, then streamable_http_client is used with Bearer token.
- Given `TASK_REGISTRY_COMMAND=python -m task_registry`, when `_connect()` runs, then stdio_client is used (existing path).
- Given both `TASK_REGISTRY_URL` and `TASK_REGISTRY_COMMAND` set, when `_connect()` runs, then ValueError is raised.
- Given neither URL nor command set, when `_connect()` runs, then ValueError is raised (no transport configured).

### Story 53.2: Auth Token Resolution

**Title:** Add _get_auth_token() method for MCP client auth

**Description:** Add `_get_auth_token()` method that reads JWT token for MCP client auth. Token source: `MCP_AUTH_TOKEN` env var (pre-generated token) OR generate from `JWT_SECRET_KEY`. Both spawners need the token to pass in Authorization header.

**Acceptance criteria:**
1. `_get_auth_token()` reads `MCP_AUTH_TOKEN` or generates from `JWT_SECRET_KEY`.
2. Token passed in `Authorization: Bearer` header.
3. Both spawners use same resolution logic.
4. When no token available and URL mode: raise clear error (not silent fallback, NFR-R15).

**Size:** S
**FR/NFR reference:** FR124, NFR-R15
**ATDD contracts:**
- Given `MCP_AUTH_TOKEN=eyJ...`, when `_get_auth_token()` runs, then the pre-generated token is returned.
- Given `JWT_SECRET_KEY=<valid>` but no `MCP_AUTH_TOKEN`, when `_get_auth_token()` runs, then a token is generated from the secret.
- Given neither `MCP_AUTH_TOKEN` nor `JWT_SECRET_KEY`, when `_get_auth_token()` runs in URL mode, then a clear error is raised (not a silent fallback to stdio).

### Story 53.3: Client Transport Tests

**Title:** Unit + integration tests for client-side dual transport

**Description:** Unit + integration tests for client-side dual transport: stdio connection works (existing), streamable-http connection with valid token, streamable-http connection without token fails fast, mutual exclusion (both URL and command set = error), server unreachable = clear error (NFR-R15).

**Acceptance criteria:**
1. Unit tests for transport branch logic.
2. Integration test for stdio path (existing).
3. Integration test for streamable-http path (with auth).
4. Test mutual exclusion validation.
5. Test unreachable server error message.

**Size:** M
**FR/NFR reference:** FR124, NFR-R15
**ATDD contracts:**
- Given stdio mode, when connecting, then ClientSession is established over stdio.
- Given streamable-http mode with valid token, when connecting, then ClientSession is established over HTTP.
- Given streamable-http mode without token, when connecting, then a clear error is raised immediately.
- Given streamable-http mode, when the server is unreachable, then a clear error message is returned (not a silent fallback).

---

## Epic 54: Docker Compose Profile (backlog)

**Goal.** Add `remote-mcp` profile to docker-compose.yml. Each MCP server gets a profile-gated service variant with `MCP_TRANSPORT=streamable-http`, `MCP_PORT`, and `JWT_SECRET_KEY`. All on internal network only (no `ports:` mapping to host, NFR-S13). Default compose (no profile) is unchanged (NFR-M10).

**FRs covered:** FR126
**NFRs:** NFR-S13

### Story 54.1: Remote-MCP Compose Profile

**Title:** Add remote-mcp profile to docker-compose.yml

**Description:** Add `remote-mcp` profile to docker-compose.yml. For each MCP server, add a profile-gated service variant with `MCP_TRANSPORT=streamable-http`, `MCP_PORT`, `JWT_SECRET_KEY`. All on internal network only (no `ports:` mapping to host, NFR-S13). Add environment variable documentation to .env.example.

**Acceptance criteria:**
1. `docker compose --profile remote-mcp config` validates.
2. No external port mappings (Docker internal network only).
3. JWT_SECRET_KEY forwarded to MCP servers only (not to workers).
4. Default compose (no profile) unchanged.
5. .env.example updated with `MCP_TRANSPORT`, `MCP_PORT`, `MCP_AUTH_TOKEN` vars.

**Size:** M
**FR/NFR reference:** FR126, NFR-S13
**ATDD contracts:**
- Given `docker compose --profile remote-mcp config`, when validated, then the config is valid.
- Given the remote-mcp profile services, when inspected, then no `ports:` mappings to host exist.
- Given the default profile (no `--profile`), when `docker compose config` runs, then it is identical to Phase 9.
- Given the remote-mcp profile, when inspecting worker services, then `JWT_SECRET_KEY` is absent from worker env.

### Story 54.2: Compose Profile Validation Tests

**Title:** Test compose config for both profiles

**Description:** Test that compose config validates correctly for both profiles. Verify no external ports in remote-mcp profile. Verify default profile has no streamable-http env vars. Verify MCP server containers have correct environment.

**Acceptance criteria:**
1. `docker compose config` validates (default profile).
2. `docker compose --profile remote-mcp config` validates.
3. No `ports:` mappings in remote-mcp profile.
4. Default profile = zero env var changes from Phase 9.

**Size:** S
**FR/NFR reference:** FR126, NFR-S13
**ATDD contracts:**
- Given default compose config, when diffed against Phase 9 config, then zero changes.
- Given remote-mcp compose config, when `ports:` keys are searched, then none are found mapped to host.
- Given remote-mcp compose config, when MCP server services are inspected, then `MCP_TRANSPORT=streamable-http` is set.

---

## Epic 55: Separability + Validation (backlog)

**Goal.** Extended separability tests for dual-transport mode (stdio-only, streamable-http, mixed). Full CI validation confirming all gates pass. Phase 10 retrospective documenting stories shipped, FR/NFR coverage, and carry-forward items. NFR-M10 verified: default deployment identical to Phase 9.

**FRs covered:** FR126
**NFRs:** NFR-M10

### Story 55.1: Extended Separability Tests

**Title:** Add separability test scenarios for dual-transport mode

**Description:** Add separability test scenarios for dual-transport mode: (a) stdio-only compose (existing, unchanged), (b) streamable-http compose profile with auth, (c) mixed mode (some servers stdio, some streamable-http). Verify health probes, task lifecycle, event emission in all modes.

**Acceptance criteria:**
1. Separability test for stdio-only (existing baseline).
2. Separability test for streamable-http mode.
3. Separability test for mixed mode.
4. All modes pass health probes.
5. Event emission verified in all modes.

**Size:** L
**FR/NFR reference:** FR126
**ATDD contracts:**
- Given stdio-only compose, when a task lifecycle runs, then events are emitted and the task completes.
- Given streamable-http compose, when a task lifecycle runs with valid JWT, then events are emitted and the task completes.
- Given mixed-mode compose, when a task spans stdio and HTTP servers, then events are emitted and the task completes.
- Given any mode, when health probes are checked, then all services report healthy.

### Story 55.2: Full CI Validation

**Title:** Run all gates and verify NFR-M10

**Description:** Run all gates: ruff (0 errors), mypy (0 errors), pytest (all pass), check_mcp_transport.py (self-test + scan), check_single_writer.py, check_tier_declarations.py, check_imports.py. Verify NFR-M10 (default deployment identical to Phase 9).

**Acceptance criteria:**
1. `just lint` EXIT 0.
2. All discipline scripts EXIT 0.
3. All tests pass.
4. Default compose config identical to Phase 9.
5. No new dependencies without ADR.

**Size:** S
**FR/NFR reference:** FR126, NFR-M10
**ATDD contracts:**
- Given `just lint`, when run, then exit code is 0.
- Given `check_mcp_transport.py`, when run (self-test + scan), then exit code is 0.
- Given default `docker compose config`, when diffed against Phase 9 snapshot, then zero differences.

### Story 55.3: Phase 10 Retrospective

**Title:** Produce Phase 10 retrospective

**Description:** Produce Phase 10 retrospective documenting: stories shipped, epics completed, FR/NFR coverage, carry-forward items (OAuth 2.1, split deployment, mTLS), lessons learned. Update sprint-status.yaml with phase-10-complete audit trail.

**Acceptance criteria:**
1. Retrospective produced.
2. sprint-status.yaml updated.
3. FR/NFR coverage verified (all mapped).
4. Carry-forward items documented.
5. Phase 10 marked complete.

**Size:** S
**FR/NFR reference:** FR126
**ATDD contracts:**
- Given the retrospective, when FR/NFR coverage is checked, then all 5 FRs and 5 NFRs are mapped to epics.
- Given sprint-status.yaml, when inspected, then phase-10-complete is recorded with audit trail.
- Given the retrospective, when carry-forward items are checked, then OAuth 2.1, split deployment, and mTLS are listed.

---

## Cross-Epic Dependencies

| Dependency | Reason |
|------------|--------|
| Epic 50 (Auth) -> Epic 52 (Server) | Server needs BearerTokenMiddleware for HTTP mode |
| Epic 51 (CI Gate) -> Epic 52 (Server) | Server changes must pass CI gate |
| Epic 52 (Server) -> Epic 53 (Client) | Client needs running HTTP server to connect to |
| Epic 52 + 53 -> Epic 54 (Compose) | Compose profile needs both server and client sides |
| Epic 54 -> Epic 55 (Validation) | Separability tests need compose profile |

## Requirements Coverage Matrix

| Requirement | Epic(s) | Fully Covered |
|-------------|---------|---------------|
| FR122 | Epic 52 | Yes |
| FR123 | Epic 50 | Yes |
| FR124 | Epic 53 | Yes |
| FR125 | Epic 51 | Yes |
| FR126 | Epics 54, 55 | Yes |
| NFR-S13 | Epic 54 | Yes |
| NFR-S14 | Epic 50 | Yes |
| NFR-M10 | Epic 55 | Yes |
| NFR-O19 | Epic 52 | Yes |
| NFR-R15 | Epic 53 | Yes |

**6 epics, 15 stories, FR122-FR126 + NFR-S13/S14/M10/O19/R15 = 100% mapped, zero orphans.**
