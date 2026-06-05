# Phase 4 Scope Extension — Browser Automation Plane

> **Status:** Phase-4 PRD amendment. Formalizes the browser-automation-plane decision from the Phase-3 retrospective research and the operator's Phase-4 convergence. FR/NFR numbering continues the canonical series (FR77 → FR78; NFR-O11 → NFR-O12; NFR-M8 → M9; NFR-S12 → S13; NFR-R8 → R9). Greek-letter epic labels continue the Phase-3 convention (Epic 20 = omega, the last letter, reflecting that browser automation was the last plane deferred from Phase 1).
>
> **Selected via:** Phase-3 retrospective readiness assessment + operator convergence on the Playwright MCP transport decision. Core decision: use **Microsoft Playwright MCP** (`@playwright/mcp`) as the primary browser transport; the server wraps Playwright's tools with oh-my-bmad's tier system, `trace_id` enforcement, and event telemetry. `browser-harness` (raw CDP) remains a future Tier-3 escape hatch — NOT Phase 4 scope. Browser server runs as a stdio subprocess (ADR-0010 recipe), containerized with `--isolated` and `--sandbox`.

**Theme:** the **browser automation plane** — a first-class, stdio-only, tier-authz'd MCP tool server that gives the worker/orchestrator structured, deterministic browser automation capabilities built on the Phase-2 spine (event-only telemetry, `trace_id`, supply-chain pipeline) and the Phase-3 fleet recipe (ADR-0010).

**Resolved scope (operator convergence, D5–D8):**

- **D5 (IN).** One browser MCP server — `browser-mcp` — wrapping Playwright MCP with oh-my-bmad's tier system, event spine integration, and container sandboxing. Navigation, interaction, screenshot, tab management, and session-isolation tools.
- **D6 (IN).** Container sandboxing — browser server runs in Docker with the `mcr.microsoft.com/playwright/mcp` base image, no host network, resource limits (memory, CPU). Chromium's own sandbox is NOT disabled (`--no-sandbox` is never passed); Docker provides the process-level sandboxing (seccomp, user namespaces).
- **D7 (OUT, deferred).** `browser-harness` (raw CDP) as an alternative transport. Remains a future Tier-3 escape hatch; not in Phase 4 scope.
- **D8 (entry point).** A **container-sandbox + separability warm-up epic FIRST** (FR87 + S-10), then the navigation/interaction tools, then the session-isolation and origin-control policies.

**Preserved invariants (carry from Phases 1–3 — non-negotiable):**

- **Single-writer (FR26) unchanged.** The browser server is a *read-only consumer* of the event log; any mutation routes through the existing `registry-state` write path. Browser state (cookies, storage) lives in the container — never the registry DB.
- **MCP transport remains stdio-only.** The browser server is a stdio MCP server spawned as a subprocess; no HTTP/SSE/streamable transport is introduced. Remote-MCP stays deferred (Phase 2 D2).
- **Event-only telemetry (NFR-O1/O10) unchanged.** The browser server emits typed events on the event spine (`browser.*`); metrics remain *derived* in `metrics-subscriber`. No per-server instrumentation.
- **`trace_id` propagation (NFR-O7) unchanged.** Every browser tool stamps/propagates `trace_id` on every event it emits.
- **Tier-enforced authz (Epic 6) unchanged.** Every destructive browser tool (`browser_evaluate`) is **Tier-3 gated** through the existing approval flow. Tab create/close are Tier-2 (local-lifecycle, no external mutation).
- **Supply-chain (Epic 8 + G-SEC-1/2) unchanged.** The browser-mcp Python code ships inside the base image (`Dockerfile.base:38` COPY, `:41` uv sync — same as all fleet servers per ADR-0010 step 7). At runtime, the browser server spawns the `mcr.microsoft.com/playwright/mcp` Docker image as a managed subprocess (`docker run -i --rm`), pinned by digest. No new Dockerfile per server, no new release.yml matrix row. The external image reference is a runtime dependency, not a build artifact.

---

## Phase 4 Functional Requirements

### ω — Browser server scaffold (Epic 20)

- **FR78.** Platform ships a stdio MCP server `mcp-servers/browser/` (package `browser-mcp`) following the ADR-0010 recipe steps 1–8: synchronous `build_server` factory, module-level `TIER_MAP`, `validate_caller_trace_id` on every tool, child-env allowlist, separability test (S-10), and mutation-gate coverage. The server delegates browser operations to the Playwright MCP subprocess (`@playwright/mcp`) which it manages as a child process; browser-mcp itself is the tier-enforcing, event-emitting, trace-id-stamping wrapper. The server emits `browser.*` events for every tool invocation and is a new separability entry (S-10).

  **Acceptance criteria:**
  - `mcp-servers/browser/` directory exists with `pyproject.toml`, `src/browser_mcp/`, standard ADR-0010 layout.
  - `build_server()` factory returns a `FastMCP` instance; lifespan handles Playwright subprocess startup/recovery.
  - `TIER_MAP` covers every tool; `check_tier_declarations.py` AST gate passes.
  - `validate_caller_trace_id` byte-identical with fleet contract tests.
  - Child-env allowlist contains only browser-specific vars (`WORKER_BROWSER_COMMAND`); no broad secrets.
  - Separability test S-10 green: toggling spawn command disables browser capability without modifying any other service.
  - `browser.session_started` event emitted on first tool invocation with `trace_id`, `session_id`, `isolated` flag, `task_id`.

### ω-2 — Navigation tools (Epic 21)

- **FR79.** Platform exposes navigation tools: `browser_navigate` (navigate to URL), `browser_navigate_back` (go back in history), `browser_snapshot` (return accessibility tree as structured, deterministic output for LLM consumption). All three are **Tier-1** (read-like; they change browser state but perform no external mutation beyond HTTP GET). Output is structured JSON (not raw HTML), optimized for LLM tool-result consumption. The server emits `browser.navigated` events carrying `url`, `status_code`, `trace_id`.

  **Acceptance criteria:**
  - `browser_navigate` accepts `url` + `caller_trace_id`; navigates; returns `{url, title, status_code, accessibility_tree_summary}`.
  - `browser_navigate_back` returns the same structured shape for the previous page.
  - `browser_snapshot` returns the current page's accessibility tree as structured JSON without navigation.
  - All three emit `browser.navigated` (or `browser.action_completed` for snapshot-only) with `trace_id`.
  - `TIER_MAP` maps all three to `Tier.ONE`.
  - Integration test: navigate to a local test fixture page; assert structured output contains expected fields.

### ω-3 — Interaction tools (Epic 22)

- **FR80.** Platform exposes interaction tools: `browser_click`, `browser_type`, `browser_fill`, `browser_select_option`, `browser_press_key`, `browser_hover`. All are **Tier-2** (side-effects on external state — they submit forms, click buttons, change UI state on remote services). Each tool emits a `browser.action_completed` event carrying `tool_name`, `success`, `duration_ms`, `trace_id`.

  **Acceptance criteria:**
  - Each tool accepts element selector (CSS/XPath), `caller_trace_id`, and tool-specific params (`text` for type/fill, `key` for press_key, `values` for select_option).
  - Each emits `browser.action_completed` with `trace_id`.
  - `TIER_MAP` maps all six to `Tier.TWO`.
  - Negative test: calling a Tier-2 tool without Tier-2 authorization returns `CapabilityDenied`.
  - Integration test: fill a form on a local test fixture page; submit; assert `browser.tool_executed` events emitted in correct order.

### ω-4 — Screenshot capture (Epic 22, shared)

- **FR81.** Platform exposes `browser_take_screenshot`. **Tier-1** (read-like; captures current viewport state). Output is saved to the artifact store path via the `artifact-mcp` integration (NFR-B3), and the tool returns the content-addressed artifact reference (SHA-256 hash + store path), not the raw image bytes. Supports PNG and JPEG formats via a `format` parameter (default: PNG).

  **Acceptance criteria:**
  - `browser_take_screenshot` accepts `format` (png/jpeg), `caller_trace_id`; returns `{artifact_ref, content_hash, format, size_bytes}`.
  - Screenshot bytes are written to artifact store via content-addressed put; raw bytes never appear in the tool result or event payload.
  - `browser.screenshot_captured` event emitted with `artifact_ref`, `content_hash`, `trace_id` — metadata-only payload (no image data in events, mirroring artifact/memory store pattern from Phase 3).
  - Integration test: take screenshot of a local test fixture page; retrieve via artifact store; assert image is valid PNG/JPEG.
  - Artifact store integration verified: screenshot appears in `artifact list` output for the task.

### ω-5 — JavaScript execution (Epic 23)

- **FR82.** Platform exposes `browser_evaluate` for executing arbitrary JavaScript in the browser context. **Tier-3** (RCE-equivalent in browser context — can exfiltrate data, modify page state, trigger network requests). Requires explicit operator approval event before execution, identical to `git push` gating (FR38). The server emits `browser.action_completed` events with `trace_id`, `tool_name="browser_evaluate"`, `success`, and a truncated result preview (max 500 chars; full result available via artifact store if operator opts in). A **negative denial test** proves the tool is denied without approval (same gate pattern as `git push` per NFR-B5).

  **Acceptance criteria:**
  - `browser_evaluate` accepts `expression` (JS code), `caller_trace_id`; returns `{result_type, result_preview, artifact_ref?}`.
  - Tool is **Tier-3 gated**: calling without a matching `approval.granted` event returns `CapabilityDenied` + emits `capability.denied` audit event.
  - Negative test: assert `CapabilityDenied` on `browser_evaluate` without approval.
  - `browser.action_completed` event emitted with `trace_id`, `tool_name="browser_evaluate"`, `expression_hash` (SHA-256 of expression for audit — never the raw expression in the event).
  - Result preview truncated to 500 chars; full result optionally stored in artifact store.
  - Integration test: evaluate `document.title` on a local test fixture page; assert structured result returned after approval grant.

### ω-6 — Tab management (Epic 23, shared)

- **FR83.** Platform exposes `browser_tabs` tool supporting operations: `list` (enumerate open tabs), `create` (open new tab), `close` (close a tab), `select` (switch to a tab). Read operations (`list`, `select`) are **Tier-1**; mutating operations (`create`, `close`) are **Tier-2** (they alter browser session state but perform no external mutation).

  **Acceptance criteria:**
  - `browser_tabs` accepts `action` (list/create/close/select), `caller_trace_id`, and action-specific params (`url` for create, `tab_id` for close/select).
  - `TIER_MAP` maps `list`/`select` to `Tier.ONE`; `create`/`close` to `Tier.TWO`.
  - Returns structured tab list: `[{tab_id, url, title, active}]`.
  - `browser.action_completed` event emitted for each action with `trace_id`.

### ω-7 — Session isolation (Epic 24)

- **FR84.** Each task's browser session runs in `--isolated` mode by default: in-memory profile, no persistent cookies, no local storage, no session state survives browser process exit. Explicit opt-in for persistent sessions is available via `--storage-state` parameter, which requires **Tier-2** authorization and persists cookies/localStorage to a task-scoped file. The isolation default ensures no state leaks between tasks (NFR-B2).

  **Acceptance criteria:**
  - Default session: no cookies/localStorage/sessionStorage persist after browser process exits.
  - Isolation verification test: task A sets a cookie; task B starts; assert task B cannot read task A's cookie.
  - `--storage-state` opt-in: when provided, cookies/localStorage persist to `<artifact_store>/browser-state/<task-id>/storage-state.json`.
  - `browser.session_started` event includes `isolated: true/false` flag.
  - Tier-2 authorization required for persistent-session opt-in.

### ω-8 — Origin control (Epic 24, shared)

- **FR85.** Browser server supports `--allowed-hosts` / `--blocked-origins` configuration per task, restricting which domains the browser may navigate to. Default: allow all (Phase 4 MVP — operator configurable). Navigation to a blocked origin returns a structured error and emits `browser.navigation_blocked` event. Origin control is enforced at the `browser_navigate` level; it does not block subresource loads (CSS/JS/images) which is a future hardening item.

  **Acceptance criteria:**
  - `browser_navigate` checks destination URL against `allowed_hosts` (allowlist) and `blocked_origins` (denylist).
  - Navigation to blocked origin returns `{blocked: true, reason, requested_url}`.
  - `browser.navigation_blocked` event emitted with `trace_id`, `requested_url`, `reason`.
  - Default: no restrictions (all origins allowed).
  - Configuration passed per-task via spawn parameters (not global env).
  - Integration test: configure `allowed_hosts=["localhost"]`; assert navigation to `http://localhost:port` succeeds; assert navigation to `https://example.com` is blocked.

### ω-9 — Browser events (cross-cutting, all Epics)

- **FR86.** Browser server emits `browser.*` event types on the event spine, registered additively in `registry-state` `domain/event_types.py`. Event types include:
  - `browser.session_started` — `{task_id, session_id, isolated, trace_id}`
  - `browser.navigated` — `{task_id, url, status_code, trace_id}`
  - `browser.action_completed` — `{task_id, tool_name, success, duration_ms, trace_id}`
  - `browser.screenshot_captured` — `{task_id, artifact_ref, content_hash, trace_id}`
  - `browser.navigation_blocked` — `{task_id, requested_url, reason, trace_id}`
  - `browser.session_ended` — `{task_id, session_id, reason, duration_s, trace_id}`

  All payloads are **metadata-only** — no page content, no screenshot bytes, no JS results in events. Content lives in the artifact store (mirrors the Phase-3 artifact/memory store pattern). Events carry `trace_id` per NFR-O7 and are derived from the event spine, not from browser stdout.

  **Acceptance criteria:**
  - All six event types registered in `domain/event_types.py` with additive schema.
  - Cardinality baseline captured; cardinality ratchet test green.
  - Zero page-content strings in any event payload (verified by schema validation test).
  - Every event carries non-null `trace_id` (AST gate enforced).

### ω-10 — Container sandboxing (Epic 25)

- **FR87.** Browser server spawns the `mcr.microsoft.com/playwright/mcp` Docker image as a managed subprocess at runtime, pinned by digest. Container configuration: no host network access (`--network` set to dedicated bridge network), memory limit (default 512 MB, operator-configurable), CPU limit (default 1.0 core). The `--no-sandbox` flag is NOT passed — Docker provides process-level sandboxing (seccomp, user namespaces). The Playwright container is NOT a compose service — it is spawned on-demand by the browser-mcp server via `docker run -i --rm --init` (the browser-mcp Python code itself ships in the base image per ADR-0010, same as all fleet servers).

  **Acceptance criteria:**
  - Spawn command is `docker run -i --rm --init mcr.microsoft.com/playwright/mcp@sha256:...` — no Dockerfile per server.
  - Container starts with memory limit, CPU limit, no host network; `--no-sandbox` is NOT in the Chromium flags.
  - Container exits cleanly on session end; no zombie processes.
  - Integration test: spawn browser container; navigate to a page; assert it works inside the sandbox; container exits on session end.
  - Base image pinned by digest in `Dockerfile.base` or config (FR56/FR77 digest discipline).

### ω-11 — Separability S-10 (Epic 20, cross-cutting)

- **FR88.** Browser capability is conditionally spawned via `WORKER_BROWSER_COMMAND` environment variable (separability S-10). Absent the variable, no browser capability is available — browser tools are not listed, browser events are never emitted, no browser container is spawned. Present the variable, and `browser-mcp` is spawned as a stdio subprocess. This mirrors the Phase-3 separability pattern (NFR-M8) but with a key difference: the browser server requires an external runtime (Playwright + Chromium), so its spawn configuration includes the container invocation rather than a simple Python entry point.

  **Acceptance criteria:**
  - With `WORKER_BROWSER_COMMAND` unset: all other services function normally; browser tools not available; no browser events emitted; no browser container spawned.
  - With `WORKER_BROWSER_COMMAND` set: browser tools listed and callable; browser events emitted; browser container spawned on first tool use.
  - Separability test S-10 in `tests/separability/`: toggle `WORKER_BROWSER_COMMAND` and assert the above.
  - No source-code modification to any other service required to toggle browser capability.

---

## Phase 4 Non-Functional Requirements

### Dependency discipline

- **NFR-B1.** No new third-party Python dependencies beyond `playwright` (which ships in the `mcr.microsoft.com/playwright/mcp` base image, not as a pip dependency of the platform — same pattern as `verification-mcp` using system tools). The browser-mcp server itself is stdlib-only Python wrapping the Playwright MCP subprocess. Verified by the fail-closed license gate (G-SEC-1) and the dependency-graph CI check.

### Session isolation

- **NFR-B2.** Browser sessions are ephemeral by default — no state (cookies, localStorage, sessionStorage, cache) leaks between tasks. Isolation is the default; persistent sessions are an explicit, Tier-2-authorized opt-in. Verified by an isolation test that runs two sequential tasks and asserts zero state leakage (FR84).

### Artifact integration

- **NFR-B3.** Screenshot output integrates with the `artifact-mcp` content-addressed store (FR76). Screenshots are stored as artifacts with SHA-256 content addressing; tool results return artifact references, not raw bytes. The integration is verified by a cross-server integration test: take screenshot → retrieve via `artifact get` → assert image integrity.

### Trace ID enforcement

- **NFR-B4.** All browser tools enforce `caller_trace_id` validation (NFR-O7) using the byte-identical `validate_caller_trace_id` helper. No browser tool accepts a null or missing `trace_id`. Verified by the trace-id-required AST gate and per-tool contract tests.

### Tier-3 denial gate

- **NFR-B5.** `browser_evaluate` is **Tier-3 with a negative denial test** — identical gate pattern to `git push` (NFR-S6). The test asserts `CapabilityDenied` on an unapproved `browser_evaluate` call and verifies the `capability.denied` audit event is emitted. The denial test is a CI-blocking regression test (ratchet, never lowered). This also applies to `browser_run_code` (same Tier-3 class).

### Observability (extends §Observability)

- **NFR-O12.** Browser-event cardinality baseline: the six `browser.*` event types (FR86) are registered with a captured cardinality baseline. The cardinality ratchet test enforces that no unregistered browser event type is emitted (mirrors the Phase-3 fleet cardinality discipline). The browser server adds no instrumentation to any other service; its events are the sole observability output.

### Maintainability (extends §Maintainability)

- **NFR-M9.** Browser separability: the browser server (FR78–FR88) is an **optional, swappable stdio member** — disabling it is a single change to the `WORKER_BROWSER_COMMAND` spawn configuration, with **no source-code modification** to any other service. Verified by separability test **S-10** in `tests/separability/`, continuing the S-1…S-9 series. The browser server follows the ADR-0010 recipe for all shared invariants (tiering, trace_id, allowlist, event spine).

### Security (extends §Security)

- **NFR-S13.** Browser supply-chain + sandbox policy: the browser-mcp Python code ships in the base image (ADR-0010 step 7); the Playwright MCP Docker image is a runtime dependency pinned by digest (FR56/FR77 discipline). The external image reference receives cosign keyless signature + SLSA-L2 provenance + CycloneDX SBOM per the existing release pipeline. The container runs with: no host network, memory limit, CPU limit, and Docker's default sandboxing (seccomp, user namespaces — Chromium's own sandbox is NOT disabled). Origin control (FR85) enforced at the Playwright level. Every destructive tool (`browser_evaluate`) is **Tier-3 gated** with a negative denial test. JavaScript expression payloads are never stored raw in events — only the SHA-256 hash is emitted (FR82). Verified by `just verify-images` green on the browser image + Tier-3-denial integration test + sandbox configuration audit test.

### Reliability (extends §Reliability)

- **NFR-R9.** Browser session cleanup: browser container exits cleanly within 10 seconds of session end (task completion, task stop, or orchestrator disconnect). Zombie browser processes are detected via a liveness check and force-killed after 30 seconds. The cleanup is verified by an integration test that terminates a task mid-browsing and asserts the container is gone. No Chromium process survives past the task boundary.

---

## Phase 4 Out-of-Scope (deferred)

Per the operator convergence (D5–D8):

- **`browser-harness` (raw CDP).** A direct Chrome DevTools Protocol integration remains a future Tier-3 escape hatch for advanced use cases (network interception, performance profiling, custom CDP sessions). Not in Phase 4 scope (D7).
- **Remote-MCP transport** (HTTP/SSE/streamable). MCP stays stdio-only; the browser server is a local subprocess even though it manages a container (D2, carried from Phase 2).
- **Second CLI agent** (Codex/Gemini/GLM). A single Claude Code runtime this phase (D3, carried from Phase 3).
- **Browser-based control surface.** The browser plane is a worker/orchestrator tool, not an operator-facing control surface. Web dashboards remain Phase 7 scope.
- **Visual regression testing / cross-browser testing.** Playwright's testing capabilities are not exposed as tools in Phase 4. Screenshot comparison and cross-browser runs are future enhancements.
- **File upload/download** through the browser. Not in the initial tool set; deferred to a future extension.
- **Network request interception / mocking.** CDP-level capabilities not exposed in Phase 4.
- **Mobile device emulation.** Playwright supports device emulation but this is not exposed as a Phase 4 tool.

**Phase boundary discipline:** every Phase 4 epic and story carries `phase: 4` in `sprint-status.yaml`. No `phase: 4` work merges to `main` until a Phase-4 gate ADR (`docs/adr/0014-phase-4-gate.md`, to be authored) is accepted.

---

## Phase 4 Sequencing

| Order | Epic | Item | Effort | Why this order |
|---|---|---|---|---|
| 1 | **Epic 20** | ω Browser server scaffold (FR78, S-10, NFR-M9) | ~2 days | ADR-0010 recipe setup + separability. Must land first so every later tool is born under enforcement. |
| 2 | **Epic 21** | ω-2 Navigation tools (FR79, FR86) | ~3 days | Lowest-risk tools; Tier-1 only; establishes the Playwright subprocess integration and event emission pattern. |
| 3 | **Epic 22** | ω-3/ω-4 Interaction + screenshot tools (FR80, FR81, NFR-B3) | ~4 days | Tier-2 tools + artifact-store integration. Navigation must work first (tool dependency). |
| 4 | **Epic 23** | ω-5/ω-6 JS execution + tab management (FR82, FR83, NFR-B5) | ~3 days | Tier-3 tool (`browser_evaluate`) — most security-sensitive; lands after Tier-1/2 patterns are proven. |
| 5 | **Epic 24** | ω-7/ω-8 Session isolation + origin control (FR84, FR85, NFR-B2) | ~3 days | Policy layer; composes with all tools above. |
| 6 | **Epic 25** | ω-10 Container sandboxing (FR87, NFR-S13, NFR-R9) | ~3 days | Can parallelize with Epics 21–24 (sandbox is orthogonal to tool surface), but must land before Phase 4 close. |

**Total estimated effort:** ~18 days of solo-operator work (slightly longer than Phase 3's 2-day cadence due to the external Playwright dependency and container sandboxing complexity).

---

## Phase 4 Success Criteria

Phase 4 success means **at minimum:**

1. **All FR78–FR88 implemented** and verified via the BMad workflow (sprint planning → create-story → validate-story → dev-story → code-review → testarch-automate/trace/nfr → retrospective per epic).
2. **NFR-B2 verified** — isolation test runs two sequential tasks and asserts zero browser-state leakage between them.
3. **NFR-B3 verified** — screenshot → artifact-store round-trip integration test green; raw image bytes never appear in tool results or event payloads.
4. **NFR-B5 verified** — `browser_evaluate` negative denial test passes; `CapabilityDenied` + `capability.denied` audit event asserted.
5. **NFR-M9 verified** — separability test S-10 green; toggling `WORKER_BROWSER_COMMAND` enables/disables browser capability with zero changes to other services.
6. **NFR-R9 verified** — browser container cleanup test green; no zombie Chromium processes survive past task boundary.
7. **NFR-S13 verified** — `just verify-images` passes for the browser image; sandbox configuration audit test green; Tier-3 denial test green.
8. **Phase 4 retrospective produced** (per epic) following the Cat-6 "three falsifiable outputs" rule: wrong-assumption, single-process-change, deferred-item triage.
9. **Phase 1–3 invariants regression-free** — `tests/separability/`, `tests/crash-injection/`, `tests/idempotency/`, `tests/contract/`, `tests/arch/` all green at every Phase 4 epic boundary.

---

## Phase 4 Ship-Blocker Checklist

All items must be green before Phase 4 can be declared complete. Any single blocker holds the phase.

| # | Blocker | Verification method | Owner |
|---|---|---|---|
| 1 | **All FR78–FR88 implemented with passing AC tests** | CI green on all browser-related test suites | Epic leads |
| 2 | **Separability S-10 green** — browser capability fully toggleable via `WORKER_BROWSER_COMMAND` with no other-service changes | `tests/separability/test_s10_browser.py` green | Epic 20 |
| 3 | **Tier-3 denial test for `browser_evaluate`** — negative test proves `CapabilityDenied` without approval | `tests/integration/test_browser_tier3_denial.py` green | Epic 23 |
| 4 | **Session isolation verified** — zero state leakage between sequential tasks | `tests/integration/test_browser_isolation.py` green | Epic 24 |
| 5 | **Artifact-store screenshot round-trip** — screenshot stored and retrievable via artifact-mcp | `tests/integration/test_browser_screenshot_artifact.py` green | Epic 22 |
| 6 | **Container sandbox configured** — no host network, memory/CPU limits, `--no-sandbox`, base image pinned by digest | Dockerfile audit + sandbox configuration test | Epic 25 |
| 7 | **Browser image supply-chain verified** — cosign signature + SLSA-L2 + CycloneDX SBOM | `just verify-images` green | Epic 25 |
| 8 | **Origin control enforced** — navigation to blocked origin returns structured error | `tests/integration/test_browser_origin_control.py` green | Epic 24 |
| 9 | **All six `browser.*` event types registered** — cardinality baseline captured, ratchet test green | `tests/arch/test_event_cardinality.py` green (with browser events) | Epic 20 |
| 10 | **Container cleanup verified** — no zombie browser processes after task end | `tests/integration/test_browser_cleanup.py` green | Epic 25 |
| 11 | **Phase 1–3 regression suite green** — no regressions in separability, crash-injection, idempotency, contract, or arch tests | Full CI pipeline green | CI |
| 12 | **Phase 4 retrospective produced** — three falsifiable outputs per epic | Retro documents reviewed and accepted | Operator |
| 13 | **Phase-4 gate ADR accepted** (`docs/adr/0014-phase-4-gate.md`) | ADR status = accepted | Operator |
| 14 | **G-SEC-2 verified closed** (Phase-3 carry-forward AI-16.1 — both halves closed 2026-06-05) — regression test green | `mcp-servers/github/` + `worker-wrapper` allowlist audit green | Prerequisite (already met) |

---

## Amendment Traceability

- **Core decision source:** Phase-3 retrospective readiness assessment (`phase-3-retrospective-2026-06-05.md` §6 — Phase 4 Readiness Assessment).
- **Transport decision:** Microsoft Playwright MCP (`@playwright/mcp`) as primary browser transport; `browser-harness` (raw CDP) deferred as Tier-3 escape hatch.
- **Architecture impact:** future `architecture.md` extension will document the Playwright subprocess management, the container sandbox topology, the origin-control enforcement layer, and the artifact-store screenshot integration.
- **Implementation-readiness gate:** before Phase 4 implementation begins, `bmad-check-implementation-readiness` must validate that this PRD amendment + a Phase 4 architecture amendment + a Phase 4 epics/stories decomposition are aligned. Phase 4 sprint planning cannot start until the readiness report passes.
- **Phase boundary discipline:** every Phase 4 epic and story carries `phase: 4` in `sprint-status.yaml`. No `phase: 4` work merges to `main` until a Phase-4 gate ADR (`docs/adr/0014-phase-4-gate.md`, to be authored) is accepted.
- **Carried-forward prerequisite:** G-SEC-2 agent-half closure (AI-16.1 from Phase-3 retro) must land before Phase 4 opens. The browser server's credential surface must not be built on top of an unclosed broad-token leak.

— *Amendment by R2d2, 2026-06-05, via the BMad `bmad-create-prd` workflow (Phase-4 extension; operator convergence D5–D8).*
