## Phase 4 Architecture Amendment — Browser Automation MCP Server

> **Amendment added:** 2026-06-05.
>
> **Companion documents:**
> - PRD amendment: see [`prd.md`](./prd.md) §"Phase 4 Scope Extension" (browser-automation plane).
> - Transport decision: see [`docs/adr/0013-playwright-mcp-transport.md`](../../docs/adr/0013-playwright-mcp-transport.md) (proposed) — resolves the browser-automation surface deferred in ADR-0009 §3.
> - Authoring recipe: see [`docs/adr/0010-mcp-server-authoring.md`](../../docs/adr/0010-mcp-server-authoring.md) — the Phase-3 canonical recipe reused for the `browser` fleet member.
> - Gate: see [`docs/adr/0014-phase-4-gate.md`](../../docs/adr/0014-phase-4-gate.md) (proposed) — this section is the architecture amendment its acceptance criteria require.

**Theme.** The browser automation plane — give the worker/orchestrator runtime a first-class, stdio-routed MCP tool server for structured browser interaction, built on Microsoft's `@playwright/mcp` as a managed subprocess (the **4th archetype: Browser Worker**). Phase 4 adds a **new tool archetype** and a **new fleet member**, not a new trust boundary — the browser subprocess inherits the same tier-enforced authz, event-only telemetry, `trace_id` propagation, and supply-chain discipline as the Phase-3 fleet. Every Phase-1, Phase-2, and Phase-3 invariant stands.

### Preserved invariants (Phase 1 + Phase 2 + Phase 3 carry forward)

All prior invariants stand unchanged. As they apply to the browser fleet member:

- **FR26 single-writer (P2-I1).** The browser server is not a second writer of persisted state. Read-only tools query Playwright's accessibility tree (in-process, ephemeral). Mutating *spine* events (`browser.navigated`, `browser.screenshot_captured`, `browser.action_completed`) route through the single FR26 writer (`clawhip-bridge`'s `EventLogWriter.append` — `mcp-servers/clawhip-bridge/src/clawhip_bridge_mcp/server.py:265`), via a spawned clawhip-bridge stdio client + `EmitterHolder` (the `task-registry` lifespan pattern — `app/main.py:90-152`). Screenshots and PDF outputs persist via the **artifact-mcp** server (FR76 reuse), not directly — the browser server calls `artifact.put` over its own MCP client connection, so artifact storage obeys P3-I2.
- **MCP transport stdio-only (P2-I4).** The browser server is `FastMCP(...).run()` on stdio. Playwright MCP itself is spawned as a **child subprocess** of the browser server (not as a separate compose service), so the external transport boundary remains stdio. The `--port` / HTTP standalone mode of `@playwright/mcp` is **not used** (ADR-0013).
- **Event-only telemetry (P2-I3 / NFR-O1/O10).** The browser server emits typed events on the spine; it adds **zero** instrumentation paths to any other service. Metrics for `browser.*` are derived by `metrics-subscriber` tailing the log (`architecture.md:1197-1214`), under the same bounded-cardinality discipline.
- **`trace_id` propagation (NFR-O7).** Every tool takes `caller_trace_id` as an **explicit, shape-validated input**, via the byte-identical `validate_caller_trace_id` helper threaded into `EventEnvelope.create(trace_id=...)`. The contract test `tests/contract/test_mcp_tool_schemas.py::test_validate_caller_trace_id_byte_identical_across_servers` is extended to cover all nine servers.
- **Tier-enforced authz (Epic 6 / P3-I1).** Every tool declares a tier in a module-level `TIER_MAP` and calls `check_tier` / `check_tier_with_approval`. The Playwright `--caps` flag is the **mechanism** for suppressing tools at the subprocess level; the `TIER_MAP` is the **policy** enforced at the oh-my-bmad server level (see Tool Surface Mapping below).
- **Supply-chain (Epic 8 + G-SEC-1/2).** The browser server code ships **inside the base image** (`Dockerfile.base:38` `COPY mcp-servers/`, `:41` `uv sync --all-packages --no-editable`) — so it inherits cosign/SLSA/CycloneDX attestation and the fail-closed license gate **without a new matrix entry**. The Playwright MCP npm package and its Chromium binary are pulled into the base image via a Dockerfile.base `RUN` layer (pinned hash, verified checksum); they are NOT installed at runtime. The child-env allowlist is expanded for `BROWSER_MCP_*` vars.

### New invariants (delta from P3-I1..I3)

Phase 4 introduces **three** new discipline rules on top of the preserved set.

| # | Invariant | Why |
|---|---|---|
| **P4-I1** | **Browser sessions are ephemeral — no state leaks between tasks.** The Playwright subprocess is launched with `--isolated` (profile kept in memory, never persisted to disk) and is **recreated per task** (killed and respawned, not reused). Cookie/localStorage/sessionStorage state dies with the subprocess. No `browser_set_storage_state` / `browser_storage_state` tools are exposed (the `storage` capability is suppressed). `browser_close` is called at task end as a safety net. The CI-gate is a negative test asserting no cookie/localStorage survives across two sequential task-scoped browser sessions. | Browser sessions accumulate credential-bearing state (cookies, auth tokens, localStorage). Without enforced ephemerality, a task that authenticates to a service leaks its session to the next task — a cross-task credential leak. The `--isolated` flag is the Playwright-native enforcement; `--isolated` + no `storage` cap + per-task respawn gives defense-in-depth. |
| **P4-I2** | **`browser_evaluate` is Tier-3 with `check_tier_with_approval` — the same gate as `git push`.** Tools that execute arbitrary JavaScript in the browser context (`browser_evaluate`, `browser_run_code`) are classified `Tier.THREE` in the `TIER_MAP` and require a matching `approval.granted` event before execution. This is the RCE-equivalent gate: JavaScript evaluated in the browser page context can exfiltrate cookies, modify the DOM to phish credentials, and make arbitrary network requests to the host machine's local network. The negative test (denied without `approval.granted`) mirrors `git push` and `github.*` write tools. | The Playwright docs themselves state `browser_run_code` is "RCE-equivalent — only enable it for trusted MCP clients." This is the browser-automation equivalent of `git push` or `github.pr.created` — powerful, destructive-capable, and requiring human approval. Tier-3 with the existing approval-signing protocol (ADR-0006) gives us the same human-in-the-loop gate. |
| **P4-I3** | **The Playwright subprocess runs inside a Docker container, never bare-metal on the host.** The browser server spawns the Playwright MCP subprocess via `docker run -i --rm --init mcr.microsoft.com/playwright/mcp@sha256:<pinned-digest>` (or the locally-built equivalent), not via `npx @playwright/mcp`. This provides process-level sandboxing (seccomp, user namespaces) and network isolation (Docker bridge network, no host network). The CI-gate asserts the spawn command contains `docker run` and not `npx`. Chromium runs with its own sandbox **enabled** (Docker's seccomp + user-namespace isolation replaces the need for `--no-sandbox`). | A browser subprocess with access to `file://` URLs, local network scanning, and arbitrary JavaScript evaluation is a privileged process. Running it bare-metal on the host means a compromised page (via `browser_evaluate` or a malicious navigation target) can reach the host filesystem and local network. Docker's default seccomp profile + user-namespace isolation limits the blast radius. This mirrors the verification-mcp worktree-sandboxing pattern (Epic 17) but at the process level. |

### New archetype: Browser Worker (4th archetype)

The existing three archetypes (subprocess-sandbox, REST-client, own-store — derived from the Phase-3 fleet) describe how an MCP server interacts with the outside world. Phase 4 adds a fourth:

**Browser Worker archetype:**
- **Spawns a Playwright subprocess** (via `docker run` in stdio mode), not a browser directly. The Playwright MCP server is the transport shim; oh-my-bmad's `browser` server is the policy layer.
- **Tier mapping is dual-enforced:** Playwright's `--caps` flag suppresses entire capability groups at the subprocess level (defense-in-depth); oh-my-bmad's `TIER_MAP` enforces tier policy at the server level. A tool suppressed by `--caps` is unreachable even if the `TIER_MAP` entry were misconfigured.
- **Output is structured JSON from the accessibility tree**, not raw HTML or screenshots. The Playwright MCP's `browser_snapshot` tool returns a deterministic, LLM-friendly accessibility-tree representation (roles, refs, text content). Screenshots (`browser_take_screenshot`) are optional, Tier-1, and routed to artifact-mcp for storage.
- **Lifecycle:** The Playwright subprocess is spawned on first browser tool call within a task and killed at task end (or on `browser_close`). It is not a long-running daemon. The oh-my-bmad browser server process itself follows the standard stdio MCP lifecycle (P3-I3).

### Transport decision: `@playwright/mcp` as structured transport (ADR-0013)

**Decision:** Use `@playwright/mcp` (Microsoft-maintained, ~33.5k GitHub stars) as the browser transport, NOT the previously-planned `browser-harness` upstream fork.

**Rationale for the change from `browser-harness` to `@playwright/mcp`:**
- The PRD (`prd.md:58,754`) and architecture (`architecture.md:58`) reference `browser-harness` as the Phase-4 upstream fork. `@playwright/mcp` supersedes this because:
  - It is **Microsoft-maintained** with active development and a large community.
  - Its **accessibility-snapshot model** is deterministic for LLM consumption (not screenshots/vision), matching our "structured output over raw data" principle.
  - Its **`--caps` flag** provides capability gating that maps directly to our tier system.
  - Its **`--isolated` mode** provides the session ephemerality P4-I1 requires.
  - Its **Docker image** (`mcr.microsoft.com/playwright/mcp`) provides the process sandboxing P4-I3 requires.
  - It is a **dependency, not a fork** — consumed via pinned npm package or Docker image, not maintained as an upstream fork. This simplifies the supply chain (no fork-sync burden).
- `browser-harness` is **dropped from the upstream-fork list** (`architecture.md:58`, `prd.md:352`). The Browser Worker archetype does not require an upstream fork; it consumes `@playwright/mcp` as a versioned dependency, the same way the verification server consumes `pytest` as a dependency.

**ADR-0013** is the formal decision record (status: proposed; must be `accepted` before Epic 20's first story merges).

### Security model: Tool Surface Mapping

The Playwright MCP `core` capability exposes ~20 tools. oh-my-bmad classifies each into the tier system and suppresses those that conflict with security invariants.

#### Tier-0: Read-only, zero side-effects

| Tool | Description | Notes |
|------|-------------|-------|
| `browser_snapshot` | Capture accessibility snapshot | Primary read tool. Deterministic structured output. |
| `browser_console_messages` | Get console output | Read-only observation. |
| `browser_get_config` | Get resolved config (requires `--caps=config`) | Introspection only. |

#### Tier-1: Navigation + observation, no external-state mutation

| Tool | Description | Notes |
|------|-------------|-------|
| `browser_navigate` | Navigate to a URL | No side-effects on external state. Origin-restricted via `--allowed-hosts` / `--allowed-origins`. |
| `browser_navigate_back` | Go back in history | Purely local navigation state. |
| `browser_tabs` | List/manage tabs (requires `--caps=core`) | Tab list is read-equivalent; tab selection is local state. |
| `browser_take_screenshot` | Capture screenshot (requires `--caps=core`) | Read-only capture. Output routed to artifact-mcp (FR76 reuse). |
| `browser_wait_for` | Wait for text/time | No side-effects. |
| `browser_network_requests` | List network requests | Read-only observation. |
| `browser_resize` | Resize browser window | Local display state only. |

#### Tier-2: Page interaction, side-effects on the target page only

| Tool | Description | Notes |
|------|-------------|-------|
| `browser_click` | Click an element | Side-effect: modifies target page state. |
| `browser_type` | Type text into element | Side-effect: modifies target page state. |
| `browser_fill_form` (aliased as `browser_fill`) | Fill multiple form fields | Side-effect: modifies target page state. |
| `browser_select_option` | Select dropdown option | Side-effect: modifies target page state. |
| `browser_press_key` | Press a key | Side-effect: modifies target page state. |
| `browser_hover` | Hover over an element | Side-effect: modifies target page state. |
| `browser_drag` | Drag and drop | Side-effect: modifies target page state. |
| `browser_handle_dialog` | Accept/dismiss dialogs | Side-effect: modifies target page state. |
| `browser_close` | Close browser | Local lifecycle. Tier-2 because it terminates the session (irreversible within the task). |

#### Tier-3: RCE-equivalent, requires `approval.granted`

| Tool | Description | Notes |
|------|-------------|-------|
| `browser_evaluate` | Evaluate JavaScript in page context | RCE-equivalent: arbitrary code execution in the browser sandbox. Can exfiltrate cookies, scan local network, modify DOM for phishing. `check_tier_with_approval` required (P4-I2). |
| `browser_run_code` (aliased as `browser_run_code_unsafe`) | Execute Playwright code in server process | RCE-equivalent: arbitrary code execution in the Playwright server process (not just the page sandbox). `check_tier_with_approval` required (P4-I2). |

> **Note:** `browser_file_upload` is classified Tier-3 but **deferred per PRD D7** (out-of-scope). It is not in the initial `TIER_MAP` or `--caps` set. May be added as Tier-3 in a future phase.

#### Suppressed capabilities (never enabled)

| Capability | Tools suppressed | Why |
|------------|-----------------|-----|
| `storage` | `browser_cookie_*`, `browser_localstorage_*`, `browser_sessionstorage_*`, `browser_storage_state`, `browser_set_storage_state` | P4-I1: sessions are ephemeral. Persistent storage would violate the no-state-leak invariant. |
| `network` | `browser_route`, `browser_route_list`, `browser_unroute`, `browser_network_state_set` | Network mocking modifies observed behavior in ways that are hard to audit. Use Tier-3 `browser_evaluate` for targeted interception if needed. |
| `vision` | `browser_mouse_move_xy`, `browser_mouse_click_xy`, `browser_mouse_drag_xy`, `browser_mouse_down/up`, `browser_mouse_wheel` | Coordinate-based tools are non-deterministic (depend on viewport size, resolution, rendering). The accessibility-snapshot model (`browser_snapshot` + `browser_click` by ref) is deterministic. |
| `pdf` | `browser_pdf_save` | PDF generation is not a core use case. Can be added in a later phase if needed (Tier-1, output via artifact-mcp). |
| `devtools` | `browser_start/stop_tracing`, `browser_start/stop_video`, `browser_video_chapter`, `browser_resume` | Tracing/video are operator-debugging tools, not worker-facing tools. Operator may enable them via a separate config profile, but they are not in the default `--caps` set. |
| `testing` | `browser_verify_*`, `browser_generate_locator` | Testing assertions are the domain of verification-mcp (Epic 17). Browser server provides raw interaction; verification server provides assertion semantics. |

**Default `--caps` set:** `core,config` (Tier-0 through Tier-2 tools only). Tier-3 tools (`browser_evaluate`, `browser_run_code`) are available in the `TIER_MAP` but suppressed at the Playwright subprocess level unless the operator explicitly enables them via `BROWSER_MCP_EXTRA_CAPS`. Even when enabled at the subprocess level, the `TIER_MAP` enforces `check_tier_with_approval` (dual enforcement). `browser_file_upload` is deferred per PRD D7 and not in the initial TIER_MAP.

### Fleet integration

**6th MCP fleet member** — after `git` (Epic 15), `github` (Epic 16), `verification` (Epic 17), `memory` (Epic 18), `artifact` (Epic 19).

**Conditional spawn** via `WORKER_BROWSER_COMMAND` / `WORKER_BROWSER_ARGS` in `WorkerSettings` (mirrors the blank-command toggle pattern — P3-I3 separability seam):

```python
# services/worker-wrapper/src/worker_wrapper/app/config.py
# Epic 20 — browser MCP server spawn command (latent scaffold).
# Operator opts in by setting a non-blank WORKER_BROWSER_COMMAND.
# When blank (default), the browser fleet member is not spawned and
# its tools are not listed — the worker functions without it (NFR-M8 / S-10).
# Mirrors the git/github/verification/memory/artifact blank-command toggle.
browser_command: str = ""
browser_args: list[str] = ["-m", "browser_mcp"]
```

The `MCPClientGroup._connect` expansion mirrors the existing conditional-spawn blocks (`mcp_clients.py:207-246`):

```python
# browser-mcp (conditional on a non-blank WORKER_BROWSER_COMMAND).
if self.settings.browser_command:
    await self._connect(
        "browser",
        self.settings.browser_command,
        self.settings.browser_args,
    )
```

**Note:** The `_connect` method takes only 3 positional parameters (`name`, `command`, `args`). Browser-specific env vars (`BROWSER_MCP_*`) are added to the `_ENV_ALLOWLIST` frozenset and sourced from `self.env` at the class level — the same pattern used by every other fleet member. No per-call env override is needed or supported.

**Screenshot/artifact integration (FR76 reuse):** `browser_take_screenshot` captures a PNG. The browser server writes it to a temp file and calls `artifact.put` over its own artifact-mcp client connection (spawned via `EmitterHolder`, same pattern as the clawhip-bridge client). The artifact server returns the content-addressed hash; the browser server returns the hash (not the raw bytes) to the caller. This avoids duplicating storage logic and keeps artifact retention/eviction centralized.

**Event types:** New `browser.*` events registered in `registry-state/domain/event_types.py`:

| Event type | Payload | Emitted by |
|------------|---------|------------|
| `browser.session_started` | `{task_id, session_id, isolated, trace_id}` | browser server, on Playwright subprocess spawn |
| `browser.navigated` | `{task_id, url, status_code, trace_id}` | browser server, on `browser_navigate` |
| `browser.action_completed` | `{task_id, tool_name, success, duration_ms, trace_id}` | browser server, on any tool call |
| `browser.screenshot_captured` | `{task_id, artifact_hash, trace_id}` | browser server, on `browser_take_screenshot` |
| `browser.navigation_blocked` | `{task_id, requested_url, reason, trace_id}` | browser server, on blocked navigation attempt |
| `browser.session_ended` | `{task_id, session_id, reason, duration_s, trace_id}` | browser server, on Playwright subprocess kill |

Event payloads defined in `packages/events/payloads.py`; `register()` calls in `domain/event_types.py`. Cardinality: bounded by `task_id` (not by URL or element ref), consistent with the bounded-cardinality discipline.

### The MCP-server-authoring recipe applied (ADR-0010, steps 1-8)

The browser server follows the ADR-0010 canonical recipe exactly. Step-by-step mapping:

**1. Workspace member + package layout.** `mcp-servers/browser/` (package `browser-mcp`). Standard tree:

```
mcp-servers/browser/
  pyproject.toml                         # name = "browser-mcp"; workspace member
  src/browser_mcp/
    __init__.py
    __main__.py                          # env validation + build_server + mcp.run()
    server.py                            # build_server(*) -> FastMCP factory
    handlers/tools.py                    # @mcp.tool() registrations + TIER_MAP
    adapters/clawhip_client.py           # EmitterHolder + ClawhipBridgeClient
    adapters/artifact_client.py          # artifact.put integration for screenshots
    adapters/playwright_subprocess.py    # docker run / npx spawn + lifecycle management
    test_server.py
```

No Dockerfile, no compose entry (P3-I3).

**2. The `build_server` factory.** Synchronous factory returning a configured `FastMCP`. The lifespan:
- Validates `BROWSER_MCP_ACTOR_KIND`, `BROWSER_MCP_ACTOR_ID`, `BROWSER_MCP_PLAYWRIGHT_IMAGE` env vars (exit 2 on missing).
- Spawns the Playwright subprocess (via `playwright_subprocess.py` — `docker run -i --rm --init <image>@sha256:<digest> --headless --isolated --caps=core,config`).
- Spawns the clawhip-bridge emitter client for FR26-routed audit.
- Spawns the artifact-mcp client for screenshot storage.
- Fails loud on startup error (OQ-4).

**3. Tool registration + tier-authz wrapping.** Each tool:
- Is a module-level `@mcp.tool()` with `caller_trace_id` as keyword-only required.
- Calls `validate_caller_trace_id` first.
- Calls `check_tier` for Tier-0..2, `check_tier_with_approval` for Tier-3 (P4-I2).
- Tier-3 tools additionally wrapped by `emit_capability_denied_on_deny`.
- Tools are **proxies** to the Playwright subprocess: the handler validates tier/authz, forwards the tool call to the Playwright MCP over its stdio connection, receives the structured JSON response, optionally routes output (screenshots) to artifact-mcp, emits a spine event, and returns to the caller.

**4. Event emission with `trace_id`.** All `browser.*` events emitted via the spine writer. `EventEnvelope.create(...)` with `trace_id=caller_trace_id`. New event types registered additively in `domain/event_types.py`.

**5. The child-env allowlist additions.** New vars for `BROWSER_MCP_ACTOR_KIND`, `BROWSER_MCP_ACTOR_ID`, `BROWSER_MCP_PLAYWRIGHT_IMAGE`, `BROWSER_MCP_EXTRA_CAPS`, `BROWSER_MCP_ALLOWED_HOSTS`, `BROWSER_MCP_ALLOWED_ORIGINS`. Added to `_ENV_ALLOWLIST` frozensets in both `worker-wrapper` and `orchestrator-adapter` (byte-identical, guarded by contract test). **No secrets.** The Playwright subprocess runs in Docker with no inherited host env — its environment is specified entirely via the `docker run -e` flags constructed by `playwright_subprocess.py`.

**6. The `__main__.py` entrypoint.** `python -m browser_mcp`: read env, validate REQUIRED vars, `build_server(...)`, `mcp.run()`. Wire `WORKER_BROWSER_COMMAND` / `WORKER_BROWSER_ARGS` into `WorkerSettings` (the blank-command toggle pattern).

**7. Supply-chain.** The browser server ships in the base image (P3-I3). The Playwright MCP npm package and Docker image are **pinned dependencies**:
- `mcr.microsoft.com/playwright/mcp@sha256:<pinned-digest>` in the base image build (verified checksum in `Dockerfile.base`).
- The pinned digest is updated via the existing release pipeline's dependency-bump automation.
- No new `release.yml` matrix row.

**8. Separability test (S-10).** `tests/separability/test_s10_browser_optional.py`. Toggles the `browser_command` in the worker MCP-client config:
- (a) With browser spawned: `browser_snapshot` is listed + callable; Playwright subprocess spawns and responds.
- (b) Without browser: every other MCP server still initializes; the worker completes a scripted task using only non-browser tools.
- Additionally: P4-I1 negative test (no cookie/localStorage survival across two sequential task-scoped sessions); P4-I3 negative test (spawn command contains `docker run`, not `npx`).

### Per-epic wiring decisions

**Epic 20 — Browser MCP server (ADR-0013 transport + ADR-0010 recipe).** `mcp-servers/browser/` (package `browser-mcp`). The reference Epic for the Browser Worker archetype. Key decisions:

- **Playwright-as-subprocess, not fork.** The server spawns `@playwright/mcp` via `docker run -i` (not `npx`). The oh-my-bmad server is the policy layer (tier authz, event emission, trace_id); Playwright is the transport layer (browser control, accessibility snapshots). This replaces the PRD's `browser-harness` upstream fork with a standard dependency.
- **`--caps=core,config` by default.** Only Tier-0 through Tier-2 tools are enabled at the Playwright level. `BROWSER_MCP_EXTRA_CAPS` allows the operator to add capabilities (e.g., `testing` for CI workflows), but `storage` and `network` are **blocklisted** — the server refuses to spawn Playwright with those caps (assertion in `__main__.py` startup validation).
- **`--isolated` always.** Hardcoded, not configurable. P4-I1 enforcement.
- **`--headless` always.** The server runs in a Docker container; there is no display. Headed mode is not supported.
- **Origin control.** `--allowed-origins` / `--allowed-hosts` set from `BROWSER_MCP_ALLOWED_HOSTS` / `BROWSER_MCP_ALLOWED_ORIGINS` env vars. Defaults to `*` (all origins) but operator can restrict to known safe domains.
- **Per-task lifecycle.** The `playwright_subprocess.py` module manages a dict of `{task_id: subprocess.Popen}` entries. On task start: spawn. On task end (or `browser_close`): kill + remove. The lifespan also kills any orphaned subprocesses on server shutdown.

**Epic 21 — Browser events + metrics.** Registers `browser.*` event types in `domain/event_types.py` + `packages/events/payloads.py`. Extends `metrics-subscriber` cardinality regression for the new event family. This is a thin epic — it follows the pattern established by Epics 15-19's event-registration stories, extracted as its own epic because the browser event surface is larger (5 event types) and the metrics-subscriber may need new derived metrics (browser action latency, screenshot count, session duration).

**Epic 22 — Browser CI hardening.** CI-gate additions specific to the browser archetype:
- P4-I1 ephemerality negative test (no state survival across sessions).
- P4-I3 container-spawn assertion test.
- Tier-3 denial negative tests for `browser_evaluate`, `browser_run_code` (browser_file_upload deferred).
- The P3-I1 tier-declaration gate (`scripts/check_tier_declarations.py`) green for `browser-mcp`.
- Separability S-10 green.
- Docker image digest pinning verified in CI (`scripts/check_browser_image_digest.py` — asserts the pinned digest in `Dockerfile.base` matches the latest `mcr.microsoft.com/playwright/mcp` manifest, fails on mismatch with a human-readable update instruction).

### Forward-referenced ADRs (proposed; each gates its epic)

Each lands `status: proposed` first and must be `accepted` before its owning epic's first story merges.

- **ADR-0013** — Playwright MCP as browser transport (replaces `browser-harness`; `--caps` dual-enforcement; `--isolated` ephemerality; Docker container subprocess; origin control). **Gates Epic 20.** `docs/adr/0013-playwright-mcp-transport.md`.
- **ADR-0014** — Phase 4 gate (opens Phase 4 for `main`-branch merges; lists acceptance criteria including this architecture amendment). **Gates Phase 4.** `docs/adr/0014-phase-4-gate.md`.

### Phase 4 CI-gate additions

The PR-required-checks list expands per epic:

- **Epic 20:** new separability entry S-10 green; P4-I1 ephemerality negative test; P4-I3 container-spawn assertion test; per-tool Tier-3-denial negative tests (`browser_evaluate`, `browser_run_code`); byte-identical `validate_caller_trace_id` + `_ENV_ALLOWLIST`-mirror contract tests extended to `browser-mcp`; new `browser.*` event types registered + cardinality-regression green in `metrics-subscriber`; the P3-I1 tier-declaration gate (`scripts/check_tier_declarations.py`) green for `browser-mcp`; Playwright Docker image digest pinning verified (`scripts/check_browser_image_digest.py`).
- **Epic 21:** `browser.*` event types appear in `packages/events/schema_registry.py`; `metrics-subscriber` cardinality-regression green for `browser.*` event family; no new high-cardinality labels (bounded by `task_id`).
- **Epic 22:** all Phase-4-specific CI checks green in the PR gate; nightly mutation score includes `mcp-servers/browser/` in its target set; the `--caps` blocklist enforcement test (refusing `storage` / `network` caps) green.

### Acceptance checklist (for ADR-0014 gate)

- [ ] Architecture amendment (this section) accepted; P4-I1 through P4-I3 invariants explicitly stated.
- [ ] ADR-0013 (`docs/adr/0013-playwright-mcp-transport.md`) authored and `status: accepted` — formally resolves the browser-automation surface deferred in ADR-0009.
- [ ] ADR-0014 (`docs/adr/0014-phase-4-gate.md`) authored and `status: accepted` — formally opens Phase 4 for `main`-branch merges.
- [ ] `bmad-create-epics-and-stories` has decomposed the browser-automation scope into Epic 20-22 stories.
- [ ] Each Phase 4 epic has its `phase: 4` label set in `sprint-status.yaml`.
- [ ] The PRD's `browser-harness` upstream-fork reference (`prd.md:58,352,392`) is updated to reference `@playwright/mcp` as a pinned dependency (not an upstream fork).
- [ ] `deferred-work.md` reviewed; any items now superseded by Phase 4 marked `killed: superseded_by_phase_4_epic_<n>`.

— *Amendment by R2d2, 2026-06-05, via the BMad `bmad-create-architecture` workflow (amendment mode).*
