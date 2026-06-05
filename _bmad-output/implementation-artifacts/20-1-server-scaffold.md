# Story 20.1: Server scaffold — `mcp-servers/browser/` workspace member (ADR-0010 steps 1, 2, 6)

Status: ready-for-dev

## Story

As the Phase-4 platform operator,
I want a new uv-workspace member `mcp-servers/browser/` (package `browser-mcp`) following the ADR-0010 recipe,
so that subsequent stories can wire Playwright subprocess management, browser tools, event emission, and fleet integration on a clean scaffold.

## Acceptance Criteria

1. **Given** an empty `mcp-servers/browser/` directory **When** I create the package with `pyproject.toml` (name `browser-mcp`, deps: `mcp`, `events`, `capabilities`), `src/browser_mcp/{__init__,__main__,server}.py`, `handlers/tools.py` (with empty `TIER_MAP`), `adapters/clawhip_client.py`, `adapters/artifact_client.py` (stub), `adapters/playwright_subprocess.py` (stub) **Then** `python -m browser_mcp` starts on stdio and fails loud (exit 2) on missing REQUIRED env vars (`BROWSER_MCP_ACTOR_KIND`, `BROWSER_MCP_ACTOR_ID`, `BROWSER_MCP_PLAYWRIGHT_IMAGE`); `just bootstrap-verify` import count increments and is green; no Dockerfile, no compose entry, no `release.yml` matrix row (P3-I3).

2. **Given** the `build_server(*)` factory **When** it is called with valid config **Then** it returns a configured `FastMCP` instance whose lifespan validates env vars, prepares the subprocess manager (without spawning Playwright yet — deferred to first tool call), and registers the clawhip-bridge emitter client.

3. **Given** `WORKER_BROWSER_COMMAND` / `WORKER_BROWSER_ARGS` in `WorkerSettings` **When** the spawn wiring is complete **Then** `browser_command: str = ""` and `browser_args: list[str] = ["-m", "browser_mcp"]` exist in the config, mirroring the blank-command toggle pattern from all five Phase-3 fleet servers.

## Tasks / Subtasks

- [ ] Task 1: Validate/fix existing scaffold files (AC: #1, #2)
  - [ ] 1.1 Verify `mcp-servers/browser/pyproject.toml` matches git-mcp pattern (name, deps, build-system)
  - [ ] 1.2 Verify `src/browser_mcp/__init__.py` exports `build_server` + `__version__`
  - [ ] 1.3 Verify `src/browser_mcp/__main__.py` env validation + fail-loud (exit 2) for `BROWSER_MCP_ACTOR_KIND`, `BROWSER_MCP_ACTOR_ID`, `BROWSER_MCP_PLAYWRIGHT_IMAGE`; cap blocklist check (`storage`/`network` refused)
  - [ ] 1.4 Verify `src/browser_mcp/server.py` — `build_server()` factory with clawhip-bridge lifespan (mirrors git-mcp `server.py` exactly)
  - [ ] 1.5 Verify `src/browser_mcp/handlers/tools.py` — empty `TIER_MAP: dict[str, Tier] = {}` + `register_tools` stub
  - [ ] 1.6 Verify `src/browser_mcp/adapters/clawhip_client.py` — byte-identical `ClawhipBridgeClient` + `EmitterHolder` from git-mcp
  - [ ] 1.7 Verify `src/browser_mcp/adapters/artifact_client.py` — placeholder stub (defers to Story 21.3)
  - [ ] 1.8 Verify `src/browser_mcp/adapters/playwright_subprocess.py` — placeholder stub (defers to Story 20.2)

- [ ] Task 2: Wire root pyproject.toml workspace integration (AC: #1)
  - [ ] 2.1 Add `browser-mcp = { workspace = true }` to root `[tool.uv.sources]` — **ATOMIC** with any deps reference
  - [ ] 2.2 Run `uv sync` to verify workspace resolution succeeds
  - [ ] 2.3 Verify `just bootstrap-verify` import count increments and passes

- [ ] Task 3: Add WorkerSettings spawn config (AC: #3)
  - [ ] 3.1 Add `browser_command: str = ""` and `browser_args: list[str] = ["-m", "browser_mcp"]` to `WorkerSettings` in `services/worker-wrapper/src/worker_wrapper/app/config.py`
  - [ ] 3.2 Add conditional `_connect("browser", ...)` block in `mcp_clients.py` (mirrors git/github/verification/memory/artifact pattern)
  - [ ] 3.3 Do NOT add `BROWSER_MCP_*` to `_ENV_ALLOWLIST` yet — that's Story 20.6 (separability)

- [ ] Task 4: Validate gates (AC: #1, #2, #3)
  - [ ] 4.1 `python -m browser_mcp` exits 2 on missing env vars
  - [ ] 4.2 `ruff check` + `ruff format` clean on all new files
  - [ ] 4.3 `mypy --strict` clean on `mcp-servers/browser/`
  - [ ] 4.4 Existing test suite passes (no regressions)
  - [ ] 4.5 `just bootstrap-verify` green with incremented import count

- [ ] Task 5: Commit scaffold (AC: all)
  - [ ] 5.1 Stage `mcp-servers/browser/` + root `pyproject.toml` + `uv.lock` changes
  - [ ] 5.2 Stage `services/worker-wrapper/` config + mcp_clients changes
  - [ ] 5.3 Commit with message: `feat(browser-mcp): scaffold mcp-servers/browser/ workspace member — ADR-0010 steps 1,2,6 (Story 20.1)`

## Dev Notes

### ⚠️ CRITICAL: Previous session scaffold exists but is untracked

The previous session's executor agent built the entire `mcp-servers/browser/` scaffold but it was **never committed**. The files exist as untracked in the working tree. The dev agent should **validate and fix** these files rather than creating them from scratch. Key gaps:
- Root `pyproject.toml` is **missing** `browser-mcp = { workspace = true }` in `[tool.uv.sources]` — this MUST be added atomically to avoid the uv hook deadlock (see project memory `uv-workspace-member-hook-deadlock`)
- WorkerSettings does NOT yet have `browser_command` / `browser_args`
- `_ENV_ALLOWLIST` does NOT yet have `BROWSER_MCP_*` entries (correctly deferred to Story 20.6)

### Architecture patterns to follow (byte-identical replication required)

The browser-mcp scaffold must follow the **ADR-0010 recipe** exactly as established by git-mcp (Epic 15). Key patterns that MUST be byte-identical with git-mcp:

1. **`ClawhipBridgeClient` + `EmitterHolder`** — copied verbatim from `mcp-servers/git/src/git_mcp/adapters/clawhip_client.py`. The cross-server contract test `test_clawhip_client_env_allowlist_byte_identical_across_servers` will assert byte-identity.

2. **`validate_caller_trace_id`** — copied verbatim from git-mcp's `handlers/tools.py`. The contract test `test_validate_caller_trace_id_byte_identical_across_servers` will assert byte-identity across all servers.

3. **`build_server()` factory pattern** — synchronous factory, all I/O in lifespan, `FastMCP("browser")`, same `EmitterHolder` wiring.

4. **`__main__.py` pattern** — env validation before lazy import, exit 2 on missing REQUIRED vars, `ActorKind` narrowing via `if/elif`.

### Required env vars (browser-specific)

| Var | Required | Purpose |
|-----|----------|---------|
| `BROWSER_MCP_ACTOR_KIND` | YES | `"worker"` or `"orchestrator"` — ActorKind narrowing |
| `BROWSER_MCP_ACTOR_ID` | YES | Caller identity for audit |
| `BROWSER_MCP_PLAYWRIGHT_IMAGE` | YES | Docker image ref for Playwright subprocess (pinned digest) |
| `BROWSER_MCP_EXTRA_CAPS` | no | Override default `--caps=core,config`; **blocklisted**: `storage`, `network` |
| `BROWSER_MCP_ALLOWED_HOSTS` | no | Origin allowlist (Story 20.4) |
| `BROWSER_MCP_ALLOWED_ORIGINS` | no | Origin allowlist alternate (Story 20.4) |

### Cap blocklist enforcement

`__main__.py` MUST validate that `BROWSER_MCP_EXTRA_CAPS` does NOT contain `storage` or `network` — the server refuses to start with these caps (P4-I1 / P4-I3 enforcement). This is browser-specific; git-mcp has no equivalent.

### What NOT to do

- **Do NOT** create a Dockerfile for browser-mcp (P3-I3 — ships in base image)
- **Do NOT** add a compose entry (P3-I3 — spawned as stdio subprocess)
- **Do NOT** add a `release.yml` matrix row (P3-I3)
- **Do NOT** add `BROWSER_MCP_*` to `_ENV_ALLOWLIST` yet (Story 20.6)
- **Do NOT** implement Playwright subprocess spawning (Story 20.2)
- **Do NOT** implement artifact client (Story 21.3)
- **Do NOT** use `os.environ.copy()` anywhere (the a0ca050 P0 pattern — NEVER broad env)

### Project Structure Notes

File layout mirrors git-mcp exactly:
```
mcp-servers/browser/
  pyproject.toml
  src/browser_mcp/
    __init__.py
    __main__.py
    server.py
    handlers/
      tools.py           # TIER_MAP (empty) + register_tools stub
    adapters/
      clawhip_client.py   # byte-identical copy from git-mcp
      artifact_client.py  # stub — defers to Story 21.3
      playwright_subprocess.py  # stub — defers to Story 20.2
```

### References

- [Source: docs/adr/0010-mcp-server-authoring.md] — ADR-0010 recipe (8 steps)
- [Source: docs/adr/0013-playwright-mcp-transport.md] — Transport decision (accepted)
- [Source: docs/adr/0014-phase-4-gate.md] — Phase 4 gate (accepted)
- [Source: _bmad-output/planning-artifacts/phase-4-epics.md §"Story 20.1"] — Story spec
- [Source: _bmad-output/planning-artifacts/phase-4-architecture-amendment.md §"ADR-0010 steps 1-8"] — Recipe mapping
- [Source: _bmad-output/planning-artifacts/phase-4-prd-amendment.md §FR78] — PRD requirements
- [Source: mcp-servers/git/] — Reference implementation (byte-identical patterns)
- [Source: services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py] — Spawn pattern + `_ENV_ALLOWLIST`
- [Source: services/worker-wrapper/src/worker_wrapper/app/config.py] — `WorkerSettings` blank-command toggle

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
