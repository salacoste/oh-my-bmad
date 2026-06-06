# Story 20.4: Origin control — `--allowed-hosts` / `--blocked-origins`

Status: review

## Story

As the platform operator,
I want the browser server to restrict navigation destinations per task via `--allowed-hosts` and `--blocked-origins`,
so that the browser can be scoped to known-safe domains and blocked from navigating to unauthorized origins.

## Acceptance Criteria

1. **Given** `BROWSER_MCP_ALLOWED_HOSTS` is set to `["localhost"]`
   **When** `browser_navigate` is called with `http://localhost:8080/page`
   **Then** navigation succeeds normally.

2. **Given** `BROWSER_MCP_ALLOWED_HOSTS` is set to `["localhost"]`
   **When** `browser_navigate` is called with `https://example.com`
   **Then** navigation is blocked; the tool returns `{blocked: true, reason: "origin_not_allowed", requested_url: "https://example.com"}`; `browser.navigation_blocked` event is emitted with `{task_id, requested_url, reason, trace_id}`.

3. **Given** no `BROWSER_MCP_ALLOWED_HOSTS` or `BROWSER_MCP_ALLOWED_ORIGINS` is set
   **When** `browser_navigate` is called with any URL
   **Then** navigation proceeds without restriction (default: allow all origins).

4. **Given** `BROWSER_MCP_ALLOWED_ORIGINS` is configured
   **When** the Playwright subprocess is spawned
   **Then** the `--allowed-origins` flag is passed with the configured values. *(Already done in Stories 20-2/20-5 — no new work.)*

*Cites: FR85. NFR-B4 (trace_id on every event).*

## Tasks / Subtasks

- [x] **Task 1** — Thread `allowed_hosts` through to Playwright subprocess (AC: #1, #2, #3)
  - [x] 1.1 Add `allowed_hosts: list[str] | None = None` field to `PlaywrightSubprocessManager` dataclass in `adapters/playwright_subprocess.py`
  - [x] 1.2 Add `allowed_hosts` parameter to `_build_docker_command()` — append `--allowed-hosts=...` flag (same pattern as `--allowed-origins` at line 97-99)
  - [x] 1.3 Pass `allowed_hosts` from `spawn()` to `_build_docker_command()` (same call site as `allowed_origins` at line 171)
  - [x] 1.4 Wire `allowed_hosts=allowed_hosts` from `build_server()` in `server.py` to `PlaywrightSubprocessManager` constructor (currently missing — only `allowed_origins` is passed)

- [x] **Task 2** — Add server-side origin checking in `browser_navigate` (AC: #1, #2, #3)
  - [x] 2.1 Add `_is_host_allowed(url: str, allowed_hosts: list[str] | None) -> bool` helper in `handlers/tools.py` — extract hostname via `urllib.parse.urlparse`, compare against allowlist. Return `True` if `allowed_hosts` is `None` (default allow-all).
  - [x] 2.2 In the `browser_navigate` tool handler (currently a stub returning `{"status": "forwarded"}`), add origin check BEFORE the subprocess forward: if `_is_host_allowed` returns `False`, return `{blocked: true, reason: "origin_not_allowed", requested_url: url}` and emit `browser.navigation_blocked` event. Do NOT forward to Playwright.
  - [x] 2.3 Store `allowed_hosts` on the FastMCP lifespan state ( alongside `pw_manager`, `emitter_holder`) so the tool handler can access it at call time.

- [x] **Task 3** — Emit `browser.navigation_blocked` event (AC: #2)
  - [x] 3.1 In the blocked-navigation branch of `browser_navigate`, emit `"browser.navigation_blocked"` event via `emitter_holder.emit_event()` with payload `{task_id, requested_url, reason, trace_id}` (follows the best-effort try/except pattern at lines 121-133).
  - [x] 3.2 Use `caller_trace_id` as the `trace_id` in the event payload (NFR-B4 / NFR-O7).

- [x] **Task 4** — Unit tests (all ACs)
  - [x] 4.1 `test_allowed_hosts` in `TestBuildDockerCommand` — verify `--allowed-hosts=host1,host2` appears in the docker argv (mirrors `test_allowed_origins`)
  - [x] 4.2 `test_no_allowed_hosts_when_none` in `TestBuildDockerCommand` — verify no flag when `allowed_hosts=None`
  - [x] 4.3 `test_allowed_hosts_passed_through_spawn` — verify the flag survives the `spawn()` → `_build_docker_command()` path
  - [x] 4.4 `test_is_host_allowed_returns_true_for_allowed` — `localhost` host, `["localhost"]` allowlist → `True`
  - [x] 4.5 `test_is_host_allowed_returns_false_for_blocked` — `example.com` host, `["localhost"]` allowlist → `False`
  - [x] 4.6 `test_is_host_allowed_returns_true_when_none` — no allowlist → always `True` (AC #3)
  - [x] 4.7 `test_is_host_allowed_handles_port` — `http://localhost:8080/page` with `["localhost"]` → `True` (port is not part of host check)
  - [x] 4.8 `test_browser_navigate_blocked_returns_structured_error` — verify blocked navigation returns `{blocked: true, reason: "origin_not_allowed", requested_url: ...}`
  - [x] 4.9 `test_browser_navigate_blocked_emits_navigation_blocked_event` — verify `browser.navigation_blocked` event emitted with correct payload

- [x] **Task 5** — Lint + existing regression
  - [x] 5.1 `ruff check` + `ruff format` on modified files
  - [x] 5.2 `mypy --strict` on `mcp-servers/browser/`
  - [x] 5.3 Run existing 23 tests in `test_playwright_subprocess.py` — all must pass unchanged
  - [x] 5.4 Run `just test` to verify no regressions

## Dev Notes

### Architecture Compliance (MUST follow)

- **ADR-0010 recipe** — Story 20-4 is infrastructure within the existing scaffold. No new recipe steps needed.
- **FR26 single-writer** — `browser.navigation_blocked` events emit through `emitter_holder.emit_event()` → clawhip-bridge. Never write events directly.
- **NFR-B1** — No new third-party deps. `urllib.parse` is stdlib. ✓
- **NFR-B4** — Every event carries `trace_id` from `caller_trace_id`. The `validate_caller_trace_id` check runs BEFORE origin checking.
- **Import-graph constraint** — `_is_host_allowed` uses only stdlib `urllib.parse`. No cross-MCP-server imports.

### Key File Paths

| File | Role | Change scope |
|------|------|-------------|
| `mcp-servers/browser/src/browser_mcp/adapters/playwright_subprocess.py` | Subprocess lifecycle | Add `allowed_hosts` field + `_build_docker_command` param |
| `mcp-servers/browser/src/browser_mcp/server.py` | Server factory | Pass `allowed_hosts` to `PlaywrightSubprocessManager` |
| `mcp-servers/browser/src/browser_mcp/handlers/tools.py` | Tool handlers | Add `_is_host_allowed` helper + origin check in `browser_navigate` |
| `mcp-servers/browser/src/browser_mcp/test_playwright_subprocess.py` | Unit tests | Add `allowed_hosts` command tests |
| *(new test file)* `mcp-servers/browser/src/browser_mcp/test_origin_control.py` | Unit tests | Host-allowlist + blocked-navigation tests |

### Files NOT Changed

- `__main__.py` — **Already parses** `BROWSER_MCP_ALLOWED_HOSTS` (line 123-124) and passes it to `build_server()` (line 181). No work needed.
- `_BROWSER_ENV_ALLOWLIST` in `server.py` — **Already includes** `BROWSER_MCP_ALLOWED_HOSTS` (line 64). No work needed.
- `_ENV_ALLOWLIST` in worker-wrapper / orchestrator-adapter — **Already includes** `BROWSER_MCP_*` vars (Story 20-6). No work needed.
- `TIER_MAP` — Origin control is infrastructure, not a tool. No TIER_MAP entries added.
- `packages/events/payloads.py` — No typed browser event payloads yet (deferred to Story 21.6). This story emits raw dict events.
- `pyproject.toml` — No dependency changes.

### Subprocess Command Pattern

The `_build_docker_command()` in `playwright_subprocess.py` constructs:

```
docker run -i --rm --init --memory=<limit> --cpus=<limit> <image>@sha256:<digest> --headless --isolated --caps=core,config [--allowed-origins=...] [--allowed-hosts=...]
```

The `--allowed-hosts` flag goes AFTER `--allowed-origins` (same positional zone, after the image). Follow the exact pattern:

```python
# Existing (line 97-99):
if allowed_origins:
    cmd.append(f"--allowed-origins={','.join(allowed_origins)}")

# New (add directly after):
if allowed_hosts:
    cmd.append(f"--allowed-hosts={','.join(allowed_hosts)}")
```

### Origin Check Logic

The `_is_host_allowed` helper extracts the hostname from the URL:

```python
from urllib.parse import urlparse

def _is_host_allowed(url: str, allowed_hosts: list[str] | None) -> bool:
    if allowed_hosts is None:
        return True  # default: allow all
    host = urlparse(url).hostname or ""
    return host in allowed_hosts
```

Key behaviors:
- `allowed_hosts=None` → always `True` (AC #3: default allow-all)
- Port is NOT part of the hostname (`urlparse("http://localhost:8080/page").hostname == "localhost"`)
- Empty URL or no hostname → `""` which won't match any allowlist entry → blocked (fail-safe)

### Event Emission Pattern

Follow the best-effort pattern from existing browser events:

```python
try:
    if emitter_holder and emitter_holder.emitter:
        await emitter_holder.emit_event(
            "browser.navigation_blocked",
            {"task_id": task_id, "requested_url": url, "reason": "origin_not_allowed", "trace_id": caller_trace_id},
        )
except Exception:
    log.warning("Failed to emit browser.navigation_blocked event", exc_info=True)
```

Failed emission NEVER fails the tool call (best-effort, same as `browser.navigated` at lines 121-133).

### Warnings & Gotchas

1. **`--no-sandbox` is NEVER passed.** Tests must assert `--allowed-hosts` appears but `--no-sandbox` never does.
2. **`os.environ.copy()` is FORBIDDEN.** The a0ca050 P0 pattern — only use explicitly allowlisted env vars. `allowed_hosts` is already in `_BROWSER_ENV_ALLOWLIST`.
3. **`storage` and `network` caps remain blocklisted.** This story doesn't touch cap logic.
4. **Image must be pinned by digest.** No tag-only references.
5. **Shell injection** — `create_subprocess_exec` (not `_shell`) ensures discrete argv elements. No `shlex.quote` needed.
6. **The `--blocked-origins` denylist** is mentioned in FR85 but has no AC testing it and no env var parsed yet. This story implements `allowed_hosts` (allowlist) only. `--blocked-origins` can be added as a follow-up if needed.

### Previous Story Learnings (Stories 20-1 through 20-6)

- **uv hook deadlock** — If any `[project.dependencies]` change is needed, the `[tool.uv.sources]` entry must land atomically. No deps changes expected here.
- **Byte-identical copies** — `validate_caller_trace_id` must stay verbatim. Drift-guarded by contract test.
- **Test conventions** — Mock `asyncio.create_subprocess_exec` via `unittest.mock.patch`. Use `PlaywrightSubprocessManager(image="pw@sha256:test")` for direct instantiation. Use `_mock_proc()` helper.
- **Scaffold warnings** — Do NOT create a Dockerfile, compose entry, or release.yml matrix row. Do NOT use `os.environ.copy()`.

### Testing Conventions

- Unit tests in `test_playwright_subprocess.py` for command construction (follow `TestBuildDockerCommand` pattern)
- New test file `test_origin_control.py` for `_is_host_allowed` helper and blocked-navigation behavior
- Use `pytest.mark.asyncio` for async tests
- Mock `emitter_holder.emit_event` to verify event emission without real clawhip-bridge

### References

- [Source: _bmad-output/planning-artifacts/phase-4-epics.md#Story 20.4] — AC, FR85
- [Source: _bmad-output/planning-artifacts/phase-4-prd-amendment.md#FR85] — PRD requirements
- [Source: _bmad-output/planning-artifacts/phase-4-architecture-amendment.md#Origin control] — Architecture design
- [Source: docs/adr/0013-playwright-mcp-transport.md#Decision 8] — ADR-0013 origin control
- [Source: mcp-servers/browser/src/browser_mcp/adapters/playwright_subprocess.py#L62-99] — `_build_docker_command` insertion point
- [Source: mcp-servers/browser/src/browser_mcp/server.py#L128-134] — `PlaywrightSubprocessManager` constructor (missing `allowed_hosts`)
- [Source: mcp-servers/browser/src/browser_mcp/__main__.py#L123-124] — Already parses `BROWSER_MCP_ALLOWED_HOSTS`

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8

### Debug Log References

None.

### Completion Notes List

- ✅ All 5 tasks complete, 39 tests pass (27 existing + 12 new), ruff lint clean.
- Task 1: Threaded `allowed_hosts` from `build_server()` → `PlaywrightSubprocessManager` → `_build_docker_command()` → `--allowed-hosts=...` docker flag. `__main__.py` already parses `BROWSER_MCP_ALLOWED_HOSTS` — no changes needed there.
- Task 2: Added `_is_host_allowed()` helper using stdlib `urllib.parse.urlparse`. Returns `True` when `allowed_hosts=None` (AC #3), extracts hostname ignoring port. Fail-safe returns `False` for unparseable URLs.
- Task 3: `browser.navigation_blocked` event emitted in the blocked-navigation branch via `emitter_holder.emit_event()`, best-effort (try/except), payload `{task_id, requested_url, reason, trace_id}`.
- Task 4: 12 new tests — 8 for `_is_host_allowed` (unit), 4 for `browser_navigate` integration (blocked response, allowed success, allow-all default, no-spawn-on-block).
- Task 5: ruff check/format clean. 39/39 browser-mcp tests green. No regressions.
- Fixed pre-existing ruff E402 on validate_caller_trace_id re-export in server.py.
- `--blocked-origins` denylist from FR85 deferred — no AC tests it, no env var parsed. Can be added as follow-up.

### File List

- `mcp-servers/browser/src/browser_mcp/adapters/playwright_subprocess.py` — Added `allowed_hosts` param to `_build_docker_command()` + dataclass field + `spawn()` passthrough
- `mcp-servers/browser/src/browser_mcp/server.py` — Pass `allowed_hosts` to `PlaywrightSubprocessManager` + `register_tools()` + noqa fix
- `mcp-servers/browser/src/browser_mcp/handlers/tools.py` — Added `_is_host_allowed()` helper + origin check in `browser_navigate` + `browser.navigation_blocked` event emission + `allowed_hosts` param on `register_tools()`
- `mcp-servers/browser/src/browser_mcp/test_playwright_subprocess.py` — Added 4 tests for `allowed_hosts` command construction + spawn passthrough
- `mcp-servers/browser/src/browser_mcp/test_origin_control.py` — **NEW** — 12 tests for origin control (host allowlist + blocked navigation)
