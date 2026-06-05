---
stepsCompleted:
  - step-01-validate-prerequisites
  - step-02-design-epics
  - step-03-create-stories
  - step-04-final-validation
workflowStatus: 'complete'
finalStoryCount: 17
finalEpicCount: 3
inputDocuments:
  - _bmad-output/planning-artifacts/phase-4-prd-amendment.md
  - _bmad-output/planning-artifacts/phase-4-architecture-amendment.md
  - _bmad-output/planning-artifacts/epics.md
workflowType: 'epics-and-stories'
project_name: 'oh-my-bmad'
user_name: 'R2d2'
date: '2026-06-05'
---

# oh-my-bmad — Phase 4 Epic Breakdown: Browser Automation Plane

## Overview

This document provides the epic and story decomposition for **Phase 4** of oh-my-bmad — adding browser automation as the 6th MCP fleet server. Phase 4 comprises 3 epics (20–22) and 17 stories, decomposing the Phase-4 PRD amendment (FR78–FR88, NFR-B1 through NFR-R9) and the Phase-4 architecture amendment (Browser Worker archetype, P4-I1/I2/I3 invariants, ADR-0013 transport decision).

This document **continues** the existing `epics.md` (Epic 1–19, Phases 1–3) and does not replace it.

## Requirements Inventory

### Functional Requirements

**Browser Server Scaffold (FR78):**
- **FR78.** Platform ships a stdio MCP server `mcp-servers/browser/` (package `browser-mcp`) following the ADR-0010 recipe steps 1–8: synchronous `build_server` factory, module-level `TIER_MAP`, `validate_caller_trace_id` on every tool, child-env allowlist, separability test (S-10), and mutation-gate coverage. The server delegates browser operations to the Playwright MCP subprocess (`@playwright/mcp`) which it manages as a child process via `docker run -i --rm --init`.

**Navigation Tools (FR79):**
- **FR79.** Platform exposes `browser_navigate`, `browser_navigate_back`, `browser_snapshot` — all Tier-1. Output is structured JSON from the accessibility tree, not raw HTML. The server emits `browser.navigated` events carrying `url`, `status_code`, `trace_id`.

**Interaction Tools (FR80):**
- **FR80.** Platform exposes `browser_click`, `browser_type`, `browser_fill`, `browser_select_option`, `browser_press_key`, `browser_hover` — all Tier-2. Each emits `browser.action_completed` carrying `tool_name`, `success`, `duration_ms`, `trace_id`.

**Screenshot Capture (FR81):**
- **FR81.** Platform exposes `browser_take_screenshot` — Tier-1. Output saved to artifact store via `artifact-mcp` integration. Returns content-addressed artifact reference, not raw bytes. Emits `browser.screenshot_captured`.

**JavaScript Execution (FR82):**
- **FR82.** Platform exposes `browser_evaluate` — Tier-3, approval-gated (RCE-equivalent). Requires `approval.granted` event before execution, identical to `git push` gating. Negative denial test required (NFR-B5). Result preview truncated to 500 chars; full result optionally in artifact store.

**Tab Management (FR83):**
- **FR83.** Platform exposes `browser_tabs` supporting `list`/`create`/`close`/`select`. Read ops (`list`/`select`) are Tier-1; mutating ops (`create`/`close`) are Tier-2.

**Session Isolation (FR84):**
- **FR84.** Each task's browser session runs in `--isolated` mode: in-memory profile, no persistent cookies/localStorage/sessionStorage. Isolation default ensures no state leaks between tasks (NFR-B2).

**Origin Control (FR85):**
- **FR85.** Browser server supports `--allowed-hosts` / `--blocked-origins` per task, restricting navigation destinations. Blocked navigation emits `browser.navigation_blocked` event.

**Browser Events (FR86):**
- **FR86.** Browser server emits six `browser.*` event types on the spine: `browser.session_started`, `browser.navigated`, `browser.action_completed`, `browser.screenshot_captured`, `browser.navigation_blocked`, `browser.session_ended`. All metadata-only payloads; content lives in artifact store.

**Container Sandboxing (FR87):**
- **FR87.** Browser server spawns `mcr.microsoft.com/playwright/mcp` Docker image as managed subprocess, pinned by digest. Container config: no host network, memory limit (default 512 MB), CPU limit (default 1.0 core). `--no-sandbox` is NOT passed. Not a compose service — spawned on-demand via `docker run -i --rm --init`.

**Separability S-10 (FR88):**
- **FR88.** Browser capability conditionally spawned via `WORKER_BROWSER_COMMAND` env var (separability S-10). Absent → no browser capability, no browser events, no container spawned. Present → browser tools listed and callable. Mirrors Phase-3 blank-command toggle pattern (NFR-M8).

**Total: 11 FRs (FR78–FR88).**

### Non-Functional Requirements

**Dependency Discipline (NFR-B1):**
- **NFR-B1.** No new third-party Python deps. Browser-mcp server is stdlib-only Python wrapping the Playwright MCP subprocess. Playwright ships in the Docker image, not as a pip dep.

**Session Isolation (NFR-B2):**
- **NFR-B2.** Zero state leakage between tasks. Ephemeral `--isolated` sessions with per-task subprocess respawn. Verified by sequential-task isolation test.

**Artifact Integration (NFR-B3):**
- **NFR-B3.** Screenshot output integrates with `artifact-mcp` content-addressed store. Tool results return artifact references, not raw bytes. Verified by cross-server integration test.

**Trace ID Enforcement (NFR-B4):**
- **NFR-B4.** All browser tools enforce `caller_trace_id` validation using byte-identical `validate_caller_trace_id`. No browser tool accepts null/missing `trace_id`.

**Tier-3 Denial Gate (NFR-B5):**
- **NFR-B5.** `browser_evaluate` is Tier-3 with negative denial test — identical gate to `git push`. CI-blocking ratchet, never lowered. Also applies to `browser_run_code`.

**Observability (NFR-O12):**
- **NFR-O12.** Browser-event cardinality baseline: six `browser.*` types registered, captured baseline, ratchet test. No unregistered browser event type emitted.

**Maintainability (NFR-M9):**
- **NFR-M9.** Browser separability: optional, swappable stdio member — single env var toggle (`WORKER_BROWSER_COMMAND`) with no source-code modification to other services. Verified by S-10.

**Security (NFR-S13):**
- **NFR-S13.** Browser supply-chain + sandbox: code in base image, Playwright Docker image pinned by digest, cosign/SLSA/SBOM verified, container sandbox (no host network, memory/CPU limits, seccomp), Tier-3 denial test, JS expression payloads hashed (never raw).

**Reliability (NFR-R9):**
- **NFR-R9.** Browser session cleanup: container exits within 10s of session end, zombie processes force-killed after 30s. Verified by mid-browsing termination integration test.

**Total: 9 NFRs.**

### Additional Requirements

**Architecture Requirements (from Phase-4 architecture amendment):**
1. ADR-0010 recipe steps 1–8 — browser-mcp follows all 8 steps exactly.
2. Dual enforcement: Playwright `--caps` + oh-my-bmad `TIER_MAP`.
3. 4th archetype: Browser Worker — spawns Playwright subprocess via `docker run -i --rm --init`.
4. Per-task subprocess lifecycle: `{task_id: subprocess.Popen}`.
5. Artifact-mcp client for screenshot storage (FR76 reuse).
6. Clawhip-bridge client for FR26-routed audit.
7. Default `--caps=core,config`; `BROWSER_MCP_EXTRA_CAPS` for override.
8. `storage` and `network` caps BLOCKLISTED — server refuses to spawn Playwright with them.
9. `--isolated` always; `--headless` always.
10. P4-I1/I2/I3 CI gates.
11. Docker-in-Docker prerequisite (the browser server spawns Docker containers from within the worker-wrapper container).

**Preserved invariants (Phase 1 + Phase 2 + Phase 3 carry forward):**
- FR26 single-writer — browser server routes spine mutations through clawhip-bridge.
- MCP transport stdio-only — browser server is `FastMCP(...).run()` on stdio.
- Event-only telemetry — browser server emits typed events only; no parallel instrumentation.
- `trace_id` propagation — every tool takes and validates `caller_trace_id`.
- Tier-enforced authz — every tool declares tier in `TIER_MAP`.
- Supply-chain — browser-mcp code ships in base image; Playwright image pinned by digest.

**New invariants (P4-I1 through P4-I3):**
- **P4-I1:** Browser sessions are ephemeral — no state leaks between tasks (`--isolated`, per-task respawn, `storage` cap suppressed).
- **P4-I2:** `browser_evaluate` is Tier-3 with `check_tier_with_approval` — the same gate as `git push`.
- **P4-I3:** Playwright subprocess runs inside a Docker container, never bare-metal on the host.

**Gating ADRs:**
- **ADR-0013** — Playwright MCP as browser transport (gates Epic 20).
- **ADR-0014** — Phase 4 gate (gates Phase 4 `main`-branch merges).

### FR Coverage Map

| FR | Epic | Stories | Note |
|---|---|---|---|
| FR78 | E20 | 20.1, 20.2 | Server scaffold + Playwright subprocess |
| FR79 | E21 | 21.1 | Navigation tools (Tier-1) |
| FR80 | E21 | 21.2 | Interaction tools (Tier-2) |
| FR81 | E21 | 21.3 | Screenshot capture + artifact integration |
| FR82 | E21 | 21.4 | JS execution (Tier-3, approval-gated) |
| FR83 | E21 | 21.5 | Tab management (Tier-1/2) |
| FR84 | E20 | 20.3 | Session isolation (`--isolated`) |
| FR85 | E20 | 20.4 | Origin control |
| FR86 | E21 | 21.6 | Browser event registration (6 types) |
| FR87 | E20 | 20.5 | Container sandboxing |
| FR88 | E20 | 20.6 | Separability S-10 |

**100% FR coverage confirmed — 11 FRs mapped across 3 epics, zero orphans.**

### NFR Coverage Summary

- **E20:** NFR-B1, NFR-B4, NFR-M9, NFR-S13, NFR-R9
- **E21:** NFR-B2, NFR-B3, NFR-B5, NFR-O12
- **E22:** All NFRs (verification layer)

**9 NFRs covered across 3 epics; zero orphans.**

## Epic List

Dependency graph:

```
E20 (browser scaffold + fleet integration)
  │
  ├──→ E21 (browser tools + event spine)
  │
  └──→ E22 (CI hardening + security gates)
```

**Each epic is standalone-valued:**
- E20 delivers a spawnable browser-mcp server that the worker can conditionally launch — the fleet integration is complete and S-10 separability is proved.
- E21 delivers the full browser tool surface (navigate, interact, screenshot, evaluate, tabs) with tier enforcement, event emission, and trace-id stamping.
- E22 delivers CI gates proving every Phase-4 invariant — ephemerality, Tier-3 denial, container spawn, cardinality, digest pinning.

**Epic sequencing rationale:**
- E20 must land first — the scaffold and Playwright subprocess management are prerequisites for all browser tools.
- E21 depends on E20's scaffold but delivers the tool surface incrementally (Tier-1 navigation first, then Tier-2 interaction, then Tier-3 JS execution).
- E22 can start as soon as E20 is complete but must finish after E21 for full coverage verification.

---

## Epic 20: Browser MCP Server Scaffold + Fleet Integration

**Goal.** Worker/orchestrator can spawn a browser-mcp stdio MCP server following ADR-0010 recipe; browser capability is conditionally available via `WORKER_BROWSER_COMMAND`. The server manages a Playwright subprocess lifecycle (`docker run -i --rm --init`), enforces `--isolated` + `--headless` always, blocks `storage`/`network` caps, and emits `browser.session_started`/`browser.session_ended` events. Separability S-10 proves the browser is fully optional.

**FRs covered:** FR78, FR84, FR85, FR87, FR88
**NFRs:** NFR-B1, NFR-B4, NFR-M9, NFR-S13, NFR-R9

### Story 20.1: Server scaffold — `mcp-servers/browser/` workspace member (ADR-0010 steps 1, 2, 6)

As the Phase-4 platform operator,
I want a new uv-workspace member `mcp-servers/browser/` (package `browser-mcp`) following the ADR-0010 recipe,
so that subsequent stories can wire Playwright subprocess management, browser tools, event emission, and fleet integration on a clean scaffold.

**Acceptance Criteria:**

**Given** an empty `mcp-servers/browser/` directory
**When** I create the package with `pyproject.toml` (name `browser-mcp`, deps: `mcp`, `events`, `capabilities`), `src/browser_mcp/{__init__,__main__,server}.py`, `handlers/tools.py` (with empty `TIER_MAP`), `adapters/clawhip_client.py`, `adapters/artifact_client.py`, `adapters/playwright_subprocess.py`
**Then** `python -m browser_mcp` starts on stdio and fails loud (exit 2) on missing REQUIRED env vars (`BROWSER_MCP_ACTOR_KIND`, `BROWSER_MCP_ACTOR_ID`, `BROWSER_MCP_PLAYWRIGHT_IMAGE`); `just bootstrap-verify` import count increments and is green; no Dockerfile, no compose entry, no `release.yml` matrix row (P3-I3).

**And Given** the `build_server(*)` factory
**When** it is called with valid config
**Then** it returns a configured `FastMCP` instance whose lifespan validates env vars, prepares the subprocess manager (without spawning Playwright yet — deferred to first tool call), and registers the clawhip-bridge emitter client.

**And Given** `WORKER_BROWSER_COMMAND` / `WORKER_BROWSER_ARGS` in `WorkerSettings`
**When** the spawn wiring is complete
**Then** `browser_command: str = ""` and `browser_args: list[str] = ["-m", "browser_mcp"]` exist in the config, mirroring the blank-command toggle pattern from all five Phase-3 fleet servers.

*Cites: FR78, NFR-B1, NFR-B4, P3-I3.*

### Story 20.2: Playwright subprocess lifecycle management

As the browser-mcp server,
I want a `playwright_subprocess.py` module that manages per-task Playwright MCP subprocesses via `docker run -i --rm --init`,
so that each task gets an isolated browser session spawned on demand and killed at task end, with configurable resource limits and pinned image digest.

**Acceptance Criteria:**

**Given** `playwright_subprocess.py` manages a dict of `{task_id: subprocess.Popen}` entries
**When** a browser tool is called for a task that has no active subprocess
**Then** the module spawns `docker run -i --rm --init <image>@sha256:<pinned-digest> --headless --isolated --caps=core,config` and returns a connected stdio transport; `browser.session_started` event is emitted with `{task_id, session_id, isolated: true, trace_id}`.

**And Given** a task's browser session is active
**When** the task ends (completion, stop, or `browser_close`)
**Then** the subprocess is terminated (SIGTERM, 10s graceful), then force-killed (SIGKILL after 30s if zombie), removed from the dict, and `browser.session_ended` event is emitted with `{task_id, session_id, reason, duration_s, trace_id}`.

**And Given** the `BROWSER_MCP_EXTRA_CAPS` env var is set to `storage` or `network`
**When** the server starts or spawns a subprocess
**Then** the server refuses to spawn and exits with an error — `storage` and `network` caps are blocklisted (P4-I1 / P4-I3 enforcement).

**And Given** the browser server shuts down
**When** the lifespan cleanup runs
**Then** all orphaned subprocesses are killed (SIGKILL), ensuring no Chromium processes survive past server exit (NFR-R9).

*Cites: FR78, FR87, NFR-R9, NFR-B1, P4-I1, P4-I3.*

### Story 20.3: Session isolation — `--isolated` enforcement + no-state-leak test (P4-I1)

As the platform operator,
I want every browser session to run with `--isolated` (in-memory profile, no persistent cookies/localStorage/sessionStorage) and to be respawned per-task,
so that no browser state leaks between tasks (NFR-B2).

**Acceptance Criteria:**

**Given** `--isolated` is hardcoded in the spawn command (not configurable)
**When** a browser subprocess is spawned for any task
**Then** the Playwright MCP runs with `--isolated` — profile kept in memory, never persisted to disk.

**And Given** the `storage` capability is suppressed (blocklisted)
**When** any tool attempts to call `browser_set_storage_state` or `browser_storage_state`
**Then** the tool does not exist in the Playwright subprocess (suppressed by `--caps`).

**And Given** task A sets a cookie on `http://localhost:<port>/set-cookie`
**When** task B starts a new browser session and navigates to `http://localhost:<port>/read-cookie`
**Then** task B cannot read task A's cookie — the P4-I1 negative test asserts zero state survival across two sequential task-scoped sessions.

*Cites: FR84, NFR-B2, P4-I1.*

### Story 20.4: Origin control — `--allowed-hosts` / `--blocked-origins`

As the platform operator,
I want the browser server to restrict navigation destinations per task via `--allowed-hosts` and `--blocked-origins`,
so that the browser can be scoped to known-safe domains and blocked from navigating to unauthorized origins.

**Acceptance Criteria:**

**Given** `BROWSER_MCP_ALLOWED_HOSTS` is set to `["localhost"]`
**When** `browser_navigate` is called with `http://localhost:8080/page`
**Then** navigation succeeds normally.

**And Given** `BROWSER_MCP_ALLOWED_HOSTS` is set to `["localhost"]`
**When** `browser_navigate` is called with `https://example.com`
**Then** navigation is blocked; the tool returns `{blocked: true, reason: "origin_not_allowed", requested_url: "https://example.com"}`; `browser.navigation_blocked` event is emitted with `{task_id, requested_url, reason, trace_id}`.

**And Given** no `BROWSER_MCP_ALLOWED_HOSTS` or `BROWSER_MCP_ALLOWED_ORIGINS` is set
**When** `browser_navigate` is called with any URL
**Then** navigation proceeds without restriction (default: allow all origins).

**And Given** `BROWSER_MCP_ALLOWED_ORIGINS` is configured
**When** the Playwright subprocess is spawned
**Then** the `--allowed-origins` flag is passed with the configured values.

*Cites: FR85.*

### Story 20.5: Container sandboxing — Docker run + resource limits (P4-I3)

As the platform operator,
I want the Playwright subprocess to run inside a Docker container with resource limits and no host network,
so that a compromised browser session cannot reach the host filesystem or local network (P4-I3).

**Acceptance Criteria:**

**Given** the Playwright subprocess spawn command
**When** the container is launched
**Then** the command contains `docker run -i --rm --init` with memory limit (`--memory=512m` default, configurable via `BROWSER_MCP_MEMORY_LIMIT`), CPU limit (`--cpus=1.0` default, configurable via `BROWSER_MCP_CPU_LIMIT`), and a dedicated bridge network (no `--network host`).

**And Given** the spawn command
**When** inspected
**Then** the command does NOT contain `--no-sandbox` — Docker provides process-level sandboxing (seccomp, user namespaces); Chromium's own sandbox stays enabled.

**And Given** a running browser container
**When** the session ends
**Then** the container exits cleanly; no zombie Docker processes survive. Integration test verifies: spawn → navigate → end → `docker ps` shows no running Playwright container.

**And Given** the base image reference
**When** the subprocess is spawned
**Then** the image is pinned by digest: `mcr.microsoft.com/playwright/mcp@sha256:<pinned-digest>` — no tag-only references (NFR-S13).

*Cites: FR87, NFR-S13, NFR-R9, P4-I3.*

### Story 20.6: Separability S-10 + child-env allowlist + fleet integration (ADR-0010 steps 5, 8)

As the platform operator,
I want browser capability to be fully toggleable via `WORKER_BROWSER_COMMAND` with no source-code changes to any other service,
so that I can opt in or out of browser functionality without modifying the rest of the fleet (NFR-M9).

**Acceptance Criteria:**

**Given** `tests/separability/test_s10_browser_optional.py` exists
**When** `WORKER_BROWSER_COMMAND` is set and `BROWSER_MCP_*` env vars are present
**Then** `MCPClientGroup` boots browser-mcp as the 9th stdio member; `browser_snapshot` appears in `list_tools()`; and the tool is callable end-to-end through the stdio boundary (hermetic — no live browser, returns structured response from Playwright stub or container).

**And Given** `WORKER_BROWSER_COMMAND` is blank (default)
**When** the worker boots
**Then** the 8 core MCP members initialize (`clients.browser is None`); a scripted `task_add_note` round-trip completes — proving browser-mcp is optional (NFR-M9).

**And Given** the `_ENV_ALLOWLIST` frozensets in both `worker-wrapper` and `orchestrator-adapter`
**When** the browser vars are added
**Then** `BROWSER_MCP_ACTOR_KIND`, `BROWSER_MCP_ACTOR_ID`, `BROWSER_MCP_PLAYWRIGHT_IMAGE`, `BROWSER_MCP_EXTRA_CAPS`, `BROWSER_MCP_ALLOWED_HOSTS`, `BROWSER_MCP_ALLOWED_ORIGINS` are present in both frozensets — byte-identical (guarded by `_ENV_ALLOWLIST`-mirror contract test). No broad secrets. `WORKER_BROWSER_COMMAND` and `WORKER_BROWSER_ARGS` are non-secret spawn-config vars.

**And Given** the `validate_caller_trace_id` contract tests
**When** extended to `browser-mcp`
**Then** byte-identity is verified across all nine servers.

*Cites: FR88, NFR-M9, NFR-B4, NFR-S13.*

### Epic 20 acceptance gate
- `mcp-servers/browser/` exists with ADR-0010 layout; `python -m browser_mcp` starts on stdio, fails loud on missing env.
- Playwright subprocess lifecycle: spawn on first tool call, kill at task end, zombie cleanup on server shutdown (NFR-R9).
- `--isolated` hardcoded; `storage`/`network` caps blocklisted; P4-I1 no-state-leak negative test green.
- Origin control enforced: blocked navigation returns structured error + emits `browser.navigation_blocked`.
- Container sandbox configured: `docker run`, memory/CPU limits, no host network, `--no-sandbox` NOT passed, digest-pinned image (P4-I3).
- Separability S-10 green (spawned and absent); `_ENV_ALLOWLIST`-mirror + `validate_caller_trace_id` contract tests extended to `browser-mcp`.
- `browser.session_started` / `browser.session_ended` events emitted with `trace_id`.
- No Dockerfile, no compose entry, no `release.yml` matrix row (P3-I3).
- ADR-0013 `accepted`.

---

## Epic 21: Browser Tools + Event Spine Integration

**Goal.** Worker can navigate, interact, screenshot, manage tabs, and execute JS in a browser — all tier-enforced, event-emitting, trace-id-stamped. The browser tool surface covers Tier-1 (navigation, snapshot, screenshot, tab list/select), Tier-2 (click, type, fill, select, press key, hover, tab create/close), and Tier-3 (`browser_evaluate` / `browser_run_code` with approval gating). All six `browser.*` event types are registered on the spine.

**FRs covered:** FR79, FR80, FR81, FR82, FR83, FR86
**NFRs:** NFR-B2, NFR-B3, NFR-B5, NFR-O12

### Story 21.1: Navigation tools — `browser_navigate`, `browser_navigate_back`, `browser_snapshot` (Tier-1)

As the worker/orchestrator runtime,
I want `browser_navigate`, `browser_navigate_back`, and `browser_snapshot` tools to navigate the browser and read its accessibility tree,
so that I can observe and interact with web pages in a structured, deterministic manner.

**Acceptance Criteria:**

**Given** `browser_navigate` is registered as `@mcp.tool()` with keyword-only required `caller_trace_id`
**When** called with `url="http://localhost:8080/page"` and a valid `caller_trace_id`
**Then** it calls `validate_caller_trace_id` first, then `check_tier(action="browser_navigate", ..., TIER_MAP["browser_navigate"])` at Tier-1, forwards the call to the Playwright subprocess, and returns `{url, title, status_code, accessibility_tree_summary}` — structured JSON from the accessibility tree, not raw HTML.

**And Given** `browser_navigate_back` is called
**When** it succeeds
**Then** it returns the same structured shape for the previous page.

**And Given** `browser_snapshot` is called
**When** it succeeds
**Then** it returns the current page's accessibility tree as structured JSON without navigating.

**And Given** any navigation tool call
**When** it completes
**Then** `browser.navigated` (or `browser.action_completed` for snapshot-only) event is emitted with `{task_id, url, status_code, trace_id}`.

**And Given** `TIER_MAP`
**When** inspected
**Then** all three tools map to `Tier.ONE`.

**And Given** a `caller_trace_id` is missing or null
**When** any navigation tool is called
**Then** the call fails validation (NFR-B4).

*Cites: FR79, NFR-B4, P4-I1.*

### Story 21.2: Interaction tools — click, type, fill, select, press key, hover (Tier-2)

As the worker/orchestrator runtime,
I want interaction tools to click, type, fill forms, select options, press keys, and hover over elements in the browser,
so that I can interact with web page elements and submit forms programmatically.

**Acceptance Criteria:**

**Given** each interaction tool (`browser_click`, `browser_type`, `browser_fill`, `browser_select_option`, `browser_press_key`, `browser_hover`) is registered as `@mcp.tool()` with keyword-only required `caller_trace_id`
**When** called with a valid element selector and `caller_trace_id`
**Then** each calls `validate_caller_trace_id` first, then `check_tier(action=..., ..., TIER_MAP[action])` at Tier-2, forwards the call to Playwright, and returns a structured response.

**And Given** each tool call completes
**When** the result is processed
**Then** `browser.action_completed` event is emitted with `{task_id, tool_name, success, duration_ms, trace_id}`.

**And Given** `TIER_MAP`
**When** inspected
**Then** all six interaction tools map to `Tier.TWO`.

**And Given** a Tier-2 tool is called without Tier-2 authorization
**When** `check_tier` evaluates
**Then** `CapabilityDenied` is returned (negative test).

**And Given** a local test fixture page with a form
**When** the interaction tools are used to fill and submit the form
**Then** `browser.action_completed` events are emitted in correct order for each interaction step.

*Cites: FR80, NFR-B4.*

### Story 21.3: Screenshot capture + artifact-mcp integration (Tier-1, NFR-B3)

As the worker/orchestrator runtime,
I want `browser_take_screenshot` to capture the current viewport and store it in the artifact-mcp content-addressed store,
so that screenshots are persisted durably with SHA-256 content addressing and retrievable via `artifact get` without raw image bytes in tool results or events.

**Acceptance Criteria:**

**Given** `browser_take_screenshot` is registered as `@mcp.tool()` with keyword-only required `caller_trace_id` and optional `format` (png/jpeg, default: png)
**When** called with a valid `caller_trace_id`
**Then** it calls `validate_caller_trace_id`, `check_tier` at Tier-1, captures the screenshot via Playwright, writes the bytes to a temp file, calls `artifact.put` over the artifact-mcp client connection, and returns `{artifact_ref, content_hash, format, size_bytes}` — the content-addressed hash, not the raw image bytes.

**And Given** the screenshot is stored
**When** `browser.screenshot_captured` event is emitted
**Then** the payload is metadata-only: `{task_id, artifact_ref, content_hash, trace_id}` — no image data in events (mirrors artifact/memory store pattern from Phase 3).

**And Given** a screenshot was taken for a task
**When** `artifact list` is queried for that task
**Then** the screenshot appears in the output — cross-server integration verified.

**And Given** the artifact-mcp client connection
**When** the browser server's lifespan starts
**Then** the artifact client is spawned via `EmitterHolder` (same pattern as clawhip-bridge client), and it connects to the artifact-mcp stdio server for `artifact.put` calls.

**And Given** a screenshot is taken of a local test fixture page
**When** the artifact is retrieved via `artifact get` with the content hash
**Then** the retrieved image is a valid PNG (or JPEG) matching the original capture.

*Cites: FR81, NFR-B3, P3-I2.*

### Story 21.4: JavaScript execution — `browser_evaluate` (Tier-3, approval-gated, P4-I2)

As the worker/orchestrator runtime,
I want `browser_evaluate` to execute arbitrary JavaScript in the browser page context,
so that I can run custom page-level scripts — but only after explicit operator approval, since this is RCE-equivalent in the browser sandbox.

**Acceptance Criteria:**

**Given** `browser_evaluate` is registered in `TIER_MAP` as `Tier.THREE`
**When** called without a matching `approval.granted` event
**Then** `check_tier_with_approval` returns `CapabilityDenied`; a `capability.denied` audit event is emitted via `emit_capability_denied_on_deny`; the tool returns an error — **the negative denial test passes** (NFR-B5).

**And Given** a matching `approval.granted` event exists for the task
**When** `browser_evaluate` is called with `expression` and `caller_trace_id`
**Then** the expression is forwarded to Playwright; the result is returned as `{result_type, result_preview, artifact_ref?}` where `result_preview` is truncated to 500 chars; full result is optionally stored in artifact store.

**And Given** `browser.action_completed` event
**When** emitted for `browser_evaluate`
**Then** the payload contains `{task_id, tool_name="browser_evaluate", success, duration_ms, trace_id, expression_hash}` where `expression_hash` is SHA-256 of the expression — **never the raw expression** in the event (audit-safe, NFR-S13).

**And Given** `browser_run_code` is also registered as Tier-3
**When** called without approval
**Then** the same `CapabilityDenied` + `capability.denied` audit path applies — both Tier-3 tools have identical denial gates.

**And Given** a local test fixture page
**When** `browser_evaluate` is called with `document.title` after approval grant
**Then** a structured result is returned: `{result_type: "string", result_preview: "<page title>"}`.

*Cites: FR82, NFR-B5, P4-I2, NFR-S13.*

### Story 21.5: Tab management — `browser_tabs` list/create/close/select (Tier-1/2)

As the worker/orchestrator runtime,
I want `browser_tabs` to list, create, close, and select browser tabs,
so that I can manage multiple page contexts within a single browser session.

**Acceptance Criteria:**

**Given** `browser_tabs` is registered as `@mcp.tool()` with `action` parameter (list/create/close/select) and keyword-only required `caller_trace_id`
**When** called with `action="list"`
**Then** it returns a structured tab list: `[{tab_id, url, title, active}]` at Tier-1 (`check_tier` with `TIER_MAP["browser_tabs.list"] = Tier.ONE`).

**And Given** `action="select"` with `tab_id`
**When** called
**Then** the specified tab becomes active; response confirms selection. Tier-1.

**And Given** `action="create"` with `url`
**When** called
**Then** a new tab opens at the specified URL. Tier-2 (`TIER_MAP["browser_tabs.create"] = Tier.TWO`).

**And Given** `action="close"` with `tab_id`
**When** called
**Then** the specified tab is closed. Tier-2 (`TIER_MAP["browser_tabs.close"] = Tier.TWO`).

**And Given** any tab action completes
**When** the result is processed
**Then** `browser.action_completed` event is emitted with `{task_id, tool_name, success, duration_ms, trace_id}`.

**And Given** `TIER_MAP`
**When** inspected
**Then** `browser_tabs.list` and `browser_tabs.select` map to `Tier.ONE`; `browser_tabs.create` and `browser_tabs.close` map to `Tier.TWO`.

*Cites: FR83, NFR-B4.*

### Story 21.6: Browser event registration — 6 `browser.*` event types + cardinality (NFR-O12)

As the platform event spine,
I want all six `browser.*` event types registered additively in the domain event registry,
so that browser events are first-class citizens on the spine with bounded cardinality and schema validation.

**Acceptance Criteria:**

**Given** the six event types: `browser.session_started`, `browser.navigated`, `browser.action_completed`, `browser.screenshot_captured`, `browser.navigation_blocked`, `browser.session_ended`
**When** registered in `packages/events/payloads.py` (payload models) and `registry-state/domain/event_types.py` (`register()` calls)
**Then** `scripts/check_event_registry.py` validates all six type strings against `packages/events/schema_registry.py` and exits 0.

**And Given** the event payloads
**When** inspected
**Then** all payloads are metadata-only — no page content, no screenshot bytes, no JS results. Every payload carries non-null `trace_id` (AST gate enforced).

**And Given** the cardinality baseline
**When** captured for `browser.*` events
**Then** cardinality is bounded by `task_id` (not by URL, element ref, or expression hash); the cardinality ratchet test in `metrics-subscriber` is green for the new event family.

**And Given** `metrics-subscriber`
**When** the cardinality-regression test runs with `browser.*` events included
**Then** no unregistered browser event type is emitted; no high-cardinality labels are introduced (NFR-O12).

**And Given** the `validate_caller_trace_id`-required AST gate
**When** scanning every `EventEnvelope.create(...)` callsite in `browser-mcp`
**Then** every callsite passes a non-null `trace_id` — the gate exits 0.

*Cites: FR86, NFR-O12, NFR-B4.*

### Epic 21 acceptance gate
- Navigation tools function at Tier-1: structured JSON output from accessibility tree, `browser.navigated` events emitted.
- Interaction tools function at Tier-2: `browser.action_completed` events emitted for each action.
- Screenshot capture stores via `artifact-mcp`: content-addressed hash returned, raw bytes never in tool result or events (NFR-B3).
- `browser_evaluate` Tier-3-denied without approval, permitted with it; `capability.denied` audit emitted; expression hashed in events (NFR-B5, P4-I2).
- Tab management: list/select at Tier-1, create/close at Tier-2.
- All six `browser.*` event types registered + cardinality green (NFR-O12).
- P3-I1 tier-declaration gate (`scripts/check_tier_declarations.py`) green for `browser-mcp`.
- `check_event_registry.py` green for all six event types.

---

## Epic 22: Browser CI Hardening + Security Gates

**Goal.** All Phase 4 CI gates pass — P4-I1 ephemerality, P4-I2 Tier-3 denial, P4-I3 container-spawn, separability S-10, tier declarations, cardinality, digest pinning. Every Phase-4-specific CI check is green in the PR gate. This epic is the verification layer proving the invariants claimed by Epics 20 and 21.

**FRs covered:** All FRs (CI enforcement layer)
**NFRs covered:** All NFRs (verification layer)

### Story 22.1: P4-I1 ephemerality CI gate — no-state-leak negative test

As the CI pipeline,
I want a negative test that runs two sequential task-scoped browser sessions and asserts zero cookie/localStorage/sessionStorage survival across them,
so that P4-I1 (browser sessions are ephemeral) is continuously verified and can never regress.

**Acceptance Criteria:**

**Given** `tests/integration/test_browser_ephemerality.py` exists
**When** the test runs
**Then** it (1) starts a browser session for task A, (2) navigates to a local test fixture page that sets a cookie and localStorage value, (3) ends task A's session, (4) starts a browser session for task B, (5) navigates to the same page and reads cookie/localStorage, (6) asserts both are absent/empty — zero state leakage.

**And When** the test is wired into CI
**Then** it runs as a PR-required check (CI-blocking ratchet, never lowered).

**And Given** the `storage` capability is suppressed
**When** `scripts/check_tier_declarations.py` or a browser-specific audit script runs
**Then** no `browser_set_storage_state` / `browser_storage_state` / `browser_cookie_*` / `browser_localstorage_*` tools exist in the Playwright subprocess — suppressed by `--caps`.

*Cites: FR84, NFR-B2, P4-I1.*

### Story 22.2: P4-I2 Tier-3 denial CI gate — `browser_evaluate` negative test

As the CI pipeline,
I want a negative test proving `browser_evaluate` is denied without `approval.granted` and the `capability.denied` audit event is emitted,
so that the Tier-3 denial gate (identical to `git push`) is regression-proof for the browser domain.

**Acceptance Criteria:**

**Given** `tests/integration/test_browser_tier3_denial.py` exists
**When** `browser_evaluate` is called without a matching `approval.granted` event
**Then** the call returns `CapabilityDenied` and a `capability.denied` audit event is emitted on the spine — the test asserts both.

**And Given** `browser_run_code` is also Tier-3
**When** called without approval
**Then** the same `CapabilityDenied` + `capability.denied` audit path applies — tested in the same negative test.

**And Given** a matching `approval.granted` event exists
**When** `browser_evaluate` is called
**Then** it succeeds — the positive test proves the approval-gated path works end-to-end.

**And When** the test is wired into CI
**Then** it runs as a PR-required check — mirrors the `git push` and `github.*` write-tool Tier-3 denial tests.

*Cites: FR82, NFR-B5, P4-I2.*

### Story 22.3: P4-I3 container-spawn CI gate — Docker run assertion test

As the CI pipeline,
I want a test asserting the Playwright subprocess spawn command contains `docker run` (not `npx`) and includes resource limits, no host network, and digest-pinned image,
so that P4-I3 (container sandboxing) is structurally verified and cannot regress.

**Acceptance Criteria:**

**Given** `tests/integration/test_browser_container_spawn.py` exists
**When** the spawn command is inspected
**Then** the test asserts: (1) command starts with `docker run`, (2) contains `-i --rm --init`, (3) contains `--memory=` with the configured limit, (4) contains `--cpus=` with the configured limit, (5) does NOT contain `--network host`, (6) does NOT contain `--no-sandbox`, (7) image reference contains `@sha256:` (digest-pinned, not tag-only), (8) contains `--headless --isolated`.

**And When** the test runs
**Then** it asserts the spawn command does NOT contain `npx` — proving the subprocess is containerized, not bare-metal.

**And Given** the `BROWSER_MCP_EXTRA_CAPS` env var
**When** set to `storage` or `network`
**Then** the server refuses to spawn — the blocklist enforcement test asserts this exit.

*Cites: FR87, NFR-S13, NFR-R9, P4-I3.*

### Story 22.4: Tier declarations + event cardinality CI gates

As the CI pipeline,
I want the P3-I1 tier-declaration AST gate extended to `browser-mcp` and the event-cardinality ratchet extended to cover `browser.*` events,
so that every browser tool declares a tier and every browser event type is registered and cardinality-bounded.

**Acceptance Criteria:**

**Given** `scripts/check_tier_declarations.py` AST-walks `mcp-servers/browser/handlers/tools.py`
**When** it runs
**Then** every `@mcp.tool()` registration has a matching entry in `browser-mcp`'s module-level `TIER_MAP` — the gate exits 0 (P3-I1 green for browser-mcp).

**And Given** a deliberately-untiered tool is added to `browser-mcp`
**When** the gate runs
**Then** it exits non-zero (self-test mode catches the violation).

**And Given** the event-cardinality ratchet test
**When** run with `browser.*` events included
**Then** the baseline is updated to include the six new event types; no unregistered browser event type is emitted; no high-cardinality labels (bounded by `task_id`); `metrics-subscriber` cardinality-regression green for the `browser.*` family (NFR-O12).

**And Given** `scripts/check_event_registry.py`
**When** run against the browser event types
**Then** all six `browser.*` type strings are validated in `packages/events/schema_registry.py` — exits 0.

**And Given** `scripts/checks/check_trace_id_required.py`
**When** scanning `mcp-servers/browser/`
**Then** every `EventEnvelope.create(...)` callsite passes a non-null `trace_id` — exits 0.

*Cites: FR86, NFR-O12, NFR-B4, P3-I1.*

### Story 22.5: Digest pinning + supply-chain + S-10 separability finalization

As the CI pipeline,
I want the Playwright Docker image digest verified in CI and the separability S-10 test green, confirming the browser server inherits the platform's supply-chain guarantees transitively from the signed base image,
so that the browser's external runtime dependency (Playwright + Chromium) meets the same supply-chain bar as all fleet members.

**Acceptance Criteria:**

**Given** `scripts/check_browser_image_digest.py` exists (or equivalent check)
**When** it runs in CI
**Then** it asserts the pinned digest in the browser-mcp config matches the latest `mcr.microsoft.com/playwright/mcp` manifest — exits 0 on match, fails on mismatch with a human-readable update instruction.

**And Given** `browser-mcp`'s third-party deps resolved against `uv.lock`
**When** classified
**Then** `mcp` is the only third-party dep (already in base SBOM); `events` and `capabilities` are first-party workspace packages. **Zero new third-party transitive dependencies** — NFR-B1 satisfied.

**And Given** `just verify-images`
**When** run against the base image carrying `browser-mcp`
**Then** cosign signature + SLSA-L2 provenance + CycloneDX SBOM verification passes — browser-mcp rides the base image (no new image).

**And Given** `tests/separability/test_s10_browser_optional.py`
**When** run
**Then** both SPAWNED and ABSENT states are green; no source-code modification to any other service required (NFR-M9).

**And Given** the license gate
**When** `scripts/check_sbom_licenses.py` runs
**Then** it passes — browser-mcp introduces no new components to the base SBOM (NFR-S13).

**And Given** the `_ENV_ALLOWLIST`-mirror contract test
**When** run with browser vars included
**Then** both frozensets (worker-wrapper + orchestrator-adapter) are byte-identical, including all `BROWSER_MCP_*` vars — exits 0.

**And Given** ADR-0013 (Playwright MCP transport)
**When** this story completes
**Then** ADR-0013 is `status: accepted`; ADR-0014 (Phase 4 gate) is `status: accepted`.

*Cites: FR78, FR88, NFR-B1, NFR-M9, NFR-S13.*

### Epic 22 acceptance gate
- P4-I1 ephemerality negative test green — zero state leakage across sequential task-scoped sessions.
- P4-I2 Tier-3 denial negative test green — `browser_evaluate` denied without approval, `capability.denied` audit emitted.
- P4-I3 container-spawn assertion green — `docker run` (not `npx`), resource limits, no host network, digest-pinned, `--headless --isolated`.
- Tier-declaration gate green for `browser-mcp` (P3-I1); event-registry gate green for all six `browser.*` types; cardinality ratchet green (NFR-O12).
- Digest pinning verified in CI; supply-chain inherited transitively from base image (NFR-S13, NFR-B1).
- Separability S-10 green (NFR-M9); `_ENV_ALLOWLIST`-mirror contract test green for all nine servers.
- `just verify-images` green on base image carrying `browser-mcp`.
- ADR-0013 accepted; ADR-0014 accepted.

---

## Phase 4 Ship-Blocker Checklist

Phase 4 has not shipped until every item below is green.

### Architectural commitments (preserved invariants + P4-I1/I2/I3)
- [ ] **FR26 single-writer unchanged** — browser server routes spine mutations through `clawhip-bridge`'s FR26 writer; it is never a second DB writer. (`scripts/check_single_writer.py` exit 0.)
- [ ] **MCP transport stdio-only (P2-I4)** — no `mcp.server.sse` / `streamable_http` in `browser-mcp`. (`scripts/check_mcp_transport.py` exit 0.)
- [ ] **No instrumentation outside `metrics-subscriber` (P2-I3 / NFR-O1/O10)** — browser server emits typed events only; metrics for `browser.*` are derived by `metrics-subscriber`.
- [ ] **Every browser event carries `trace_id` (NFR-O7)** — `validate_caller_trace_id` byte-identical across all nine servers; AST gate exits 0 for `browser-mcp`.
- [ ] **P3-I1 — every MCP tool declares a capability tier** — `scripts/check_tier_declarations.py` green for `browser-mcp`; every destructive tool is Tier-3 with negative denial test.
- [ ] **P3-I3 — servers ship as wheels in the base image, spawned as stdio subprocesses** — no Dockerfile, no compose entry, no `release.yml` matrix row for `browser-mcp`.
- [ ] **P4-I1 — browser sessions are ephemeral** — no state leaks between tasks; `--isolated` hardcoded; `storage` cap suppressed; negative test green.
- [ ] **P4-I2 — `browser_evaluate` is Tier-3 with `check_tier_with_approval`** — negative denial test green; mirrors `git push` gate.
- [ ] **P4-I3 — Playwright subprocess runs in Docker container** — spawn command uses `docker run`, not `npx`; no host network; resource limits; `--no-sandbox` NOT passed.
- [ ] **Supply-chain** — base image carrying all nine servers passes cosign + SLSA-L2 + CycloneDX SBOM + license gate; Playwright Docker image pinned by digest; `just verify-images` green.

### Per-epic gates
- [ ] **Epic 20** — `browser-mcp` scaffold complete; Playwright subprocess lifecycle managed; `--isolated` enforced; origin control enforced; container sandbox configured; S-10 green; `browser.session_started`/`session_ended` events emitted; ADR-0013 accepted.
- [ ] **Epic 21** — Navigation tools Tier-1 green; interaction tools Tier-2 green; screenshot → artifact-store round-trip green; `browser_evaluate` Tier-3-denied without approval green; tab management Tier-1/2 green; all six `browser.*` event types registered + cardinality green.
- [ ] **Epic 22** — P4-I1 ephemerality test green; P4-I2 Tier-3 denial test green; P4-I3 container-spawn test green; tier declarations + cardinality gates green; digest pinning verified; S-10 green; ADR-0014 accepted.

### Phase 1 + Phase 2 + Phase 3 invariants regression-free
- [ ] `tests/separability/` **S-1 through S-10** all green.
- [ ] `tests/crash-injection/` all green.
- [ ] `tests/idempotency/` all green.
- [ ] `tests/contract/` all green — `validate_caller_trace_id`-byte-identical + `_ENV_ALLOWLIST`-mirror tests extended to all nine servers.
- [ ] Arch gates (`check_{single_writer,imports,event_registry,mcp_transport,tier_declarations}.py` + `check_trace_id_required.py`) all exit 0.
- [ ] Replay / byte-for-byte equivalence holds after additive `browser.*` event-type registration.

### New ADRs accepted
- [ ] **ADR-0013** — Playwright MCP as browser transport (`status: accepted`). Gates Epic 20.
- [ ] **ADR-0014** — Phase 4 gate (`status: accepted`). Gates Phase 4 `main`-branch merges.

### Documentation
- [ ] `docs/operator-runbook.md` extended with: browser server operator notes (spawn toggle, origin control config, resource limits, digest pinning).
- [ ] `_bmad-output/project-context.md` updated with Phase 4 additions: the Browser Worker archetype, P4-I1/I2/I3 invariants, Docker-in-Docker prerequisite.
- [ ] A retrospective lands at every Phase-4 epic boundary.

### Principle

If any item above is not green/complete, **Phase 4 has not shipped**. The three new invariants (P4-I1 ephemeral sessions, P4-I2 Tier-3 denial, P4-I3 container sandbox) plus the preserved Phase-1+2+3 spine (FR26 single-writer, stdio-only transport, event-only telemetry, `trace_id` correlation, tier-enforced authz, signed supply-chain) are the contract. Phase 4 adds a **tool archetype and a fleet member**, not a new trust boundary — any untiered tool, state leak, bare-metal subprocess, or unverified image digest is a ship-blocker, not a feature.

---

*Decomposed by R2d2, 2026-06-05, via the BMad `bmad-create-epics-and-stories` workflow (Phase-4 extension mode).*
