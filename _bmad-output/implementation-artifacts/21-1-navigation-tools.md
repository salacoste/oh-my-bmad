# Story 21.1: Navigation tools — `browser_navigate`, `browser_navigate_back`, `browser_snapshot` (Tier-1)

Status: review

## Story

As the platform operator,
I want browser navigation tools (`browser_navigate`, `browser_navigate_back`, `browser_snapshot`) that proxy to a managed Playwright MCP subprocess,
so that workers can navigate web pages, go back, and capture accessibility-tree snapshots via Tier-1 authorization.

## Acceptance Criteria

1. **Given** a valid `caller_trace_id` and Tier-1 authorization
   **When** `browser_navigate` is called with `url="https://example.com"`
   **Then** the tool forwards the call to the Playwright subprocess via MCP stdio, receives a structured response, and returns `{url, title, status_code, accessibility_tree_summary}`.

2. **Given** a valid `caller_trace_id` and Tier-1 authorization
   **When** `browser_navigate_back` is called
   **Then** the tool forwards to Playwright and returns the same structured shape for the previous page.

3. **Given** a valid `caller_trace_id` and Tier-1 authorization
   **When** `browser_snapshot` is called
   **Then** the tool returns the current page's accessibility tree as structured JSON without navigating.

4. **Given** any navigation tool completes
   **When** the response is received from Playwright
   **Then** a `browser.navigated` (or `browser.action_completed` for snapshot) event is emitted with `{task_id, url, status_code, trace_id}`.

5. **Given** `TIER_MAP` is inspected
   **Then** all three tools are mapped to `Tier.ONE`.

6. **Given** a missing or invalid `caller_trace_id`
   **When** any navigation tool is called
   **Then** the call fails validation (NFR-B4).

*Cites: FR79. NFR-B4 (trace_id on every event). ADR-0010 steps 3-4.*

## Tasks / Subtasks

- [x] **Task 1** — Playwright MCP stdio client adapter (AC: #1, #2, #3)
  - [x] 1.1 Create `adapters/playwright_client.py` with `PlaywrightMCPClient` class — wraps an existing `asyncio.subprocess.Process`'s stdin/stdout as an MCP `ClientSession`
  - [x] 1.2 `PlaywrightMCPClient.__aenter__()` creates `ClientSession(read_stream, write_stream)` over the process pipes, calls `session.initialize()` with bounded timeout
  - [x] 1.3 `PlaywrightMCPClient.call_tool(name, arguments)` calls `self._session.call_tool(name, arguments)` and returns the `CallToolResult`
  - [x] 1.4 `PlaywrightMCPClient.__aexit__()` cleans up the session without killing the process (process lifecycle managed by `PlaywrightSubprocessManager`)
  - [x] 1.5 Update `PlaywrightSession` dataclass to hold an optional `PlaywrightMCPClient` alongside `proc`
  - [x] 1.6 Add `ensure_client()` → lazy-initialize `PlaywrightMCPClient` on the session after process spawn

- [x] **Task 2** — Wire tool forwarding in `handlers/tools.py` (AC: #1, #2, #3, #4)
  - [x] 2.1 Update `browser_navigate` to call `client.call_tool("browser_navigate", {"url": url})` via `ensure_client()`
  - [x] 2.2 Parse Playwright response and return `{url, title, status_code, accessibility_tree_summary}`
  - [x] 2.3 Update `browser_navigate_back` similarly — forward `browser_navigate_back` to Playwright
  - [x] 2.4 Update `browser_snapshot` — forward `browser_snapshot` to Playwright, return accessibility tree
  - [x] 2.5 Update event payloads: `browser.navigated` includes `status_code`, `browser.action_completed` includes `success`

- [x] **Task 3** — Error handling for subprocess failures (AC: #1-#3 robustness)
  - [x] 3.1 RuntimeError from client → return `{error: true, reason: "subprocess_error"}`
  - [x] 3.2 TimeoutError → return `{error: true, reason: "subprocess_timeout"}`
  - [x] 3.3 Playwright `isError` result → propagate error text in structured response

- [x] **Task 4** — Unit tests (all ACs)
  - [x] 4.1 `test_aenter_initializes_session` — mock process pipes, verify ClientSession created
  - [x] 4.2 `test_call_tool_forwards_to_session` — verify call_tool delegates with correct name/args
  - [x] 4.3 `test_allowed_navigation_succeeds` — verify browser_navigate calls ensure_client + call_tool
  - [x] 4.4 `test_call_tool_propagates_error_result` — isError=True → error in response
  - [x] 4.5 `test_blocked_does_not_spawn_subprocess` — verify ensure_client NOT called on block
  - [x] 4.6 `test_aexit_clears_session` — session cleared without killing process
  - [x] 4.7 `test_init_raises_if_no_stdin` / `test_init_raises_if_no_stdout` — pipe validation
  - [x] 4.8 `test_call_tool_raises_without_session` — RuntimeError before init
  - [x] 4.9 `test_blocked_emits_navigation_blocked_event` — event payload includes trace_id

- [x] **Task 5** — Lint + regression
  - [x] 5.1 `ruff check` clean on all modified files
  - [x] 5.2 55/55 browser-mcp tests pass — 0 regressions

## Dev Notes

### Architecture Compliance

- **ADR-0010 recipe** — Steps 1,2,5,6,7 already done (Epic 20). This story completes steps 3-4 (tool registration + forwarding + event emission).
- **FR26 single-writer** — All events via `emitter_holder.emit_event()`.
- **NFR-B1** — No new third-party deps. `mcp.ClientSession` is already a dependency (used by `ClawhipBridgeClient`).
- **Import-graph constraint** — `playwright_client.py` uses only `mcp` SDK + stdlib. No cross-MCP-server imports.

### Key Design Decision: MCP Client over Existing Process

The `PlaywrightSubprocessManager` spawns a Docker container running `@playwright/mcp` in `--headless` mode. The container exposes an MCP server over stdio. The browser-mcp tool handlers are MCP *clients* of this subprocess.

The pattern mirrors `ClawhipBridgeClient` (which connects to clawhip-bridge over stdio), but with key differences:
- `ClawhipBridgeClient` spawns its own process; `PlaywrightMCPClient` wraps an **already-spawned** process (managed by `PlaywrightSubprocessManager`)
- `ClawhipBridgeClient` lives for the server's entire lifespan; `PlaywrightMCPClient` is per-task and lazy-initialized
- `PlaywrightMCPClient.__aexit__` does NOT kill the process — only closes the MCP session

### Key File Paths

| File | Role | Change scope |
|------|------|-------------|
| `adapters/playwright_client.py` | **NEW** — MCP client over subprocess stdio | ~120 LOC |
| `adapters/playwright_subprocess.py` | Subprocess lifecycle | Update `PlaywrightSession` + `get_or_spawn` to lazy-init client |
| `handlers/tools.py` | Tool handlers | Replace stubs with real forwarding |
| `test_playwright_client.py` | **NEW** — Client adapter tests | ~150 LOC |
| `test_origin_control.py` | Origin control tests | Update to handle new forwarding path |
| `test_playwright_subprocess.py` | Subprocess tests | Update for client initialization |

### Warnings & Gotchas

1. **`mcp.ClientSession` requires readable streams** — `proc.stdout` is an `StreamReader`, which may need wrapping. Check if `mcp` SDK accepts `StreamReader` directly or needs adaptation.
2. **Playwright MCP tool names may differ** — `@playwright/mcp` uses `browser_navigate` (underscore), not `browser.navigate` (dot). Verify the actual tool names exposed by the Playwright MCP subprocess.
3. **Process pipe buffering** — Docker stdio may buffer. Use `PYTHONUNBUFFERED=1` or check if `--headless` mode flushes after each response.
4. **Concurrent tool calls** — Multiple tasks may call tools simultaneously. Each task has its own subprocess/session, so no contention. But within a single task, calls should be serialized over the MCP session.
5. **Event payload `status_code`** — Playwright MCP's `browser_navigate` response may or may not include HTTP status code. If it doesn't, use `null` and document the gap.

### Previous Story Learnings

- **Byte-identical copies** — `validate_caller_trace_id` must stay verbatim. Contract test guards it.
- **CaptureMCP pattern** — `_CaptureMCP` stub in `test_origin_control.py` is reusable for tool tests.
- **No `os.environ.copy()`** — Only use explicitly allowlisted env vars.
- **No `--no-sandbox`** — Always assert its absence.
- **Origin control runs before forwarding** — `_is_host_allowed` check stays at the top of `browser_navigate`.

### References

- [Source: _bmad-output/planning-artifacts/phase-4-epics.md#Story 21.1] — AC, FR79
- [Source: _bmad-output/planning-artifacts/phase-4-prd-amendment.md#FR79] — PRD requirements
- [Source: _bmad-output/planning-artifacts/phase-4-architecture-amendment.md#Browser Worker] — Architecture
- [Source: docs/adr/0010-mcp-server-authoring.md] — ADR-0010 recipe
- [Source: docs/adr/0013-playwright-mcp-transport.md] — ADR-0013 transport
- [Source: mcp-servers/browser/src/browser_mcp/adapters/clawhip_client.py] — MCP client pattern to mirror

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8

### Debug Log References

None.

### Completion Notes List

- ✅ All 5 tasks complete, 55 tests pass (45 existing + 10 new), ruff lint clean.
- Task 1: Created `PlaywrightMCPClient` — wraps existing subprocess pipes as MCP `ClientSession` using anyio memory object streams (same pattern as `mcp.client.stdio.stdio_client`). `ensure_client()` on `PlaywrightSubprocessManager` lazy-initializes the client on first tool call.
- Task 2: Replaced stub returns in all 3 tools with real `client.call_tool()` forwarding. Response parsing via `_parse_navigate_result()` and `_parse_snapshot_result()`. Event payloads now include `status_code` and `success`.
- Task 3: Structured error handling — `RuntimeError` → `{error: true, reason: "subprocess_error"}`, `TimeoutError` → `"subprocess_timeout"`, `isError` result → propagated error text.
- Task 4: 10 new tests for `PlaywrightMCPClient` lifecycle (init, aenter, aexit, call_tool forwarding, error propagation, pipe validation). Updated existing origin-control tests to use mock `ensure_client`.
- Task 5: ruff check clean. 55/55 browser-mcp tests green. No regressions.

### File List

- `mcp-servers/browser/src/browser_mcp/adapters/playwright_client.py` — **NEW** — PlaywrightMCPClient: MCP stdio client over existing subprocess pipes
- `mcp-servers/browser/src/browser_mcp/adapters/playwright_subprocess.py` — Updated `PlaywrightSession` with optional `client` field + `ensure_client()` method + client close in `kill_session()`
- `mcp-servers/browser/src/browser_mcp/handlers/tools.py` — Replaced stubs with real forwarding via `ensure_client()` + `_parse_navigate_result()` + `_parse_snapshot_result()` + structured error handling
- `mcp-servers/browser/src/browser_mcp/test_playwright_client.py` — **NEW** — 10 tests for PlaywrightMCPClient lifecycle
- `mcp-servers/browser/src/browser_mcp/test_origin_control.py` — Updated to use mock `ensure_client` for allowed-path tests
