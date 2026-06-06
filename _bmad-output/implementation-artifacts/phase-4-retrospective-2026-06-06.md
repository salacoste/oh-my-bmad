# Phase 4 Retrospective: Browser Automation Plane

## 1. Phase 4 Overview

**Scope:** FR78–FR88 (browser MCP server, 15 tools, container sandbox, origin control, screenshot/artifact integration, JS execution), NFR-B1–B5 (ephemerality, tool coverage, screenshot NFR, Tier-3 gating, JS timeout), NFR-O12 (event cardinality), NFR-M9 (separability S-10), NFR-S13 (digest pinning), NFR-R9 (lifecycle cleanup). Invariants: P4-I1 (session ephemerality), P4-I2 (Tier-3 approval gate), P4-I3 (container sandbox).

**Timeline:** 2026-06-05 – 2026-06-06.

**Epics:** 3 (Epic 20 through Epic 22).

**Stories:** 17 total (6 + 6 + 5).

**PRs/Commits:** ~17 merged (all direct-to-main), all CI-green.

**Phase 4 shipped:**
- A browser MCP server wrapping `@playwright/mcp` as a managed Docker subprocess — the fleet's 4th archetype (after subprocess-sandbox, REST-client, and own-store).
- 15 browser tools across 3 tiers: 4 Tier-1 (navigate, navigate_back, snapshot, take_screenshot + tab_list, tab_select), 8 Tier-2 (click, type, fill, select_option, press_key, hover, tab_create, tab_close), 1 Tier-3 (evaluate).
- Origin control (`--allowed-hosts` / `--blocked-origins`) with `browser.navigation_blocked` event emission.
- Container sandbox (memory/CPU limits, no host network, no `--no-sandbox`, `--isolated` hardcoded).
- Screenshot → artifact-mcp integration (`browser.screenshot_captured` event, content-addressed storage).
- 6 `browser.*` event types registered at schema 1.1.0 with cardinality ratchet.
- Separability S-10 (browser is the 9th optional stdio member, toggleable via `WORKER_BROWSER_COMMAND`).
- Digest-pinning gate (`check_browser_image_digest.py`) for the Playwright Docker image.
- G-SEC-2 fully closed (both MCP-subprocess and claude-agent halves; broad `GITHUB_TOKEN` removed from all child env allowlists).
- Two new ADRs: ADR-0013 (Playwright MCP transport), ADR-0014 (Phase 4 gate).

---

## 2. What Went Well

### The recipe held for a 4th archetype
ADR-0010's server-authoring recipe — scaffold, ATDD, Tier-1 tools, Tier-2/3 tools, events, separability, supply-chain — was followed verbatim for browser-mcp despite the fundamentally different execution model (Docker-in-Docker rather than local subprocess or REST client). The recipe steps mapped cleanly: `PlaywrightSubprocessManager` is `GitExecutor` with Docker argv instead of git argv; `_BROWSER_ENV_ALLOWLIST` mirrors git/github/verification allowlists; `EmitterHolder` is structurally identical. No recipe amendments were needed.

### Born-under-enforcement: all three AST gates green from story one
Browser-mcp's 15 tools were discovered by `check_tier_declarations.py` via glob — zero code changes to the gate. The 6 `browser.*` events were found by `check_event_registry.py` via string-literal scan. The new `check_browser_image_digest.py` is the only browser-specific gate. The enforcement infrastructure built in Phase 3's entry-epic pattern paid for itself again.

### The forwarding pattern scaled to 15 tools
`_forward_action_tool` (extracted in Story 21.2) reduced 10 of 15 tools to 5-line wrappers: validate, check_tier, forward, emit, return. Adding tab management (Story 21.5) took 12 lines total. The pattern is now proven across the largest tool surface in the fleet.

### Origin control is a first-class security boundary
Story 20.4 shipped origin control with 10 test cases covering exact match, subdomain isolation, trailing-dot normalization, case-insensitive matching, and fail-safe blocking on unparseable URLs. The `browser.navigation_blocked` event provides an audit trail for every denied navigation. This is a stronger security posture than the Phase-3 MCP servers, which trust their subprocess output without inspecting it.

### G-SEC-2 closure completed the last known credential-exposure gap
The audit trail entry (2026-06-05) documents the full closure: both `claude_code_runner.py` and `omc_runner.py` now have explicit "GITHUB_TOKEN is INTENTIONALLY NOT in this allowlist" comments, and regression tests in both services assert the token is absent from `_build_child_env()` output. The investigation confirmed the token was INERT in practice (local bare remote, no credential helper wired), but the defense-in-depth closure is correct.

---

## 3. What Could Be Improved

### The naming inconsistency was caught too late
`browser_take_screenshot` uses an underscore while every other tool uses a dotted name (`browser.navigate`, `browser.click`). The inconsistency passed code review in Story 21.3 because the TIER_MAP and `@mcp.tool(name=...)` registration both used the underscore form consistently — the deviation was self-consistent but wrong relative to ADR-0010. A naming-convention AST gate (analogous to the tier-declaration gate) would have caught this automatically.

### Navigation tools duplicated 150 lines before the forwarding pattern was extracted
Stories 21.1 shipped three navigation tools with full inline handler bodies (~80 lines each). Story 21.2 then extracted `_forward_action_tool` and reduced interaction tools to 5-line wrappers. The navigation tools were never retrofitted, leaving ~150 lines of near-duplicate code. The lesson: extract the shared pattern before writing the third tool that needs it, not after.

### Docker-only tests leave P4-I1 partially unproven
Two ephemerality tests (`test_cookie_not_persistent_across_sessions`, `test_localstorage_not_persistent_across_sessions`) skip in CI because the Playwright Docker image is not available. P4-I1 (zero state leakage) is proven by the `--isolated` flag in the spawn command and by structural assertions, but not by an actual cross-session state-isolation test. This is a coverage gap that can only close when CI has Docker-in-Docker or a Playwright image pull.

### The digest gate proves format, not freshness
`check_browser_image_digest.py` verifies that `@sha256:` format appears in documentation and tests, but does NOT pin a specific digest or verify against the upstream manifest. A drifted or stale digest passes the gate silently. The `--verify-remote` flag exists but requires `BROWSER_MCP_PLAYWRIGHT_IMAGE` env var + crane/skopeo, neither available in CI. The gate is format-correct but not content-correct.

---

## 4. Key Lessons Learned

1. **The recipe works for Docker-in-Docker too.** Browser-mcp wraps a container that itself spawns another container (Playwright). This adds Docker socket / DinD complexity but the structural recipe — scaffold, ATDD, tiered tools, events, separability — needed zero modifications. The archetype is "managed subprocess" regardless of whether the subprocess is local or containerized. (Epic 20)

2. **Extract the shared forwarding pattern by tool #3, not tool #6.** The navigation tools (21.1) duplicated ~150 lines that the interaction tools (21.2) avoided via `_forward_action_tool`. The trigger for extraction should be "two tools share >50% of their handler body" not "the team is tired of copy-paste." (Epic 21)

3. **A naming convention needs a gate, not just a review.** Code review catches naming deviations inconsistently. A lightweight AST gate that asserts all `@mcp.tool(name=...)` values match `server.action` (dotted, no underscores) would have caught `browser_take_screenshot` at CI time. (Epic 21)

4. **Container sandbox assertions are unit tests, not integration tests.** The 13 `_build_docker_command` security tests test a pure function with zero I/O. They should live alongside the production code, not behind integration-test infrastructure. Security invariants on internal functions must fire on every local test run. (Epic 22)

5. **CI gate scripts with structural discovery scale without modification.** `check_tier_declarations.py` found browser's 15 tools by glob without a code change. `check_event_registry.py` found 6 `browser.*` events by string scan. The lesson from Phase 3 (avoid hardcoded server lists) paid off — no server-specific branches were needed. (Epic 22)

6. **A format-only digest gate is better than no gate, but it is not a supply-chain gate.** Proving `@sha256:` exists somewhere in the repo is necessary but not sufficient. A complete gate pins a specific digest and verifies it against the upstream. The format gate is the floor; the freshness gate is the ceiling. (Epic 22)

7. **G-SEC-2 closure required investigation, not just code changes.** Dropping `GITHUB_TOKEN` from the allowlist was the correct closure, but the investigation also proved the token was inert (local bare remote, no credential helper, no token-in-URL). The investigation was necessary to distinguish "correct closure" from "correct closure of a non-threat." Always verify the threat model before closing a security gap. (Ship-blocker verification)

---

## 5. Deferred Items Carried Forward

### From Epic 20
- **P1 (carry):** Real Docker ephemerality tests — two tests in `test_browser_ephemerality.py` skip pending CI Docker availability. P4-I1 is structurally proven but not runtime-proven.
- **P2 (carry):** `browser_take_screenshot` naming fix — rename to `browser.take_screenshot` per ADR-0010 convention. Updates TIER_MAP, tests, event payloads.
- **P3 (monitor):** `_client_lock` per-manager bottleneck — single `asyncio.Lock` serializes all `ensure_client()` calls. Monitor under multi-task load; split to per-task if contention observed.

### From Epic 21
- **P1 (carry):** Navigation tools refactor to `_forward_action_tool` — `browser_navigate`, `browser_navigate_back`, `browser_snapshot` have ~150 lines of duplicated handler code. Refactor with origin-check hooks.
- **P2 (carry):** `_parse_navigate_result` / `_parse_snapshot_result` fragile regex — brittle against Playwright MCP output format changes. Replace with structured JSON when Playwright supports it.
- **P3 (monitor):** Screenshot base64 decode in-band — memory spike risk for large screenshots. Monitor for OOM; if observed, stream directly to artifact-mcp.
- **P4 (deprioritize):** `expression_hash` not reversible for registry verification. Not needed for Phase 4.

### From Epic 22
- **P1 (carry):** Digest pinning is format-only, not runtime-verified. The `--verify-remote` flag needs CI infrastructure (crane/skopeo + env var).
- **P2 (carry):** `check_browser_image_digest.py` does not pin a canonical digest. A stale digest passes the gate silently.

### From Phase 3 (still open)
- **AI-14.1:** Ratchet mutation threshold above 82%.
- **AI-14.2 / G-FN-2:** Re-enable spawner audit emission (currently disabled with `OMB_MCP_AUDIT_EMISSION_ENABLED=0`).
- **AI-15.2:** Broaden `check_tier_declarations.py` discovery beyond `handlers/tools.py`.
- **AI-15.3 + AI-17.1:** `run_git` / `run_recipe` output caps (bounded reader + kill on overflow). Unbounded `communicate()` buffers risk OOM.
- **AI-16.2:** Flip `simulate=False` and validate live GitHub write.
- **G-FN-3:** Bound liveness probes with `asyncio.wait_for`.
- **Fleet-level integration test:** No end-to-end multi-server workflow test exists.

---

## 6. Phase 5 Readiness Assessment

### What Phase 4 Established

| Capability | Status | Notes |
|---|---|---|
| Browser MCP server (4th archetype) | 15 tools, 3 tiers | Docker-in-Docker, recipe-compliant |
| Container sandbox | Memory/CPU limits, no host network | P4-I3 enforced in spawn command |
| Session ephemerality | `--isolated` hardcoded | P4-I1 structurally proven, runtime gap remains |
| Origin control | Allowed/blocked hosts per-task | 10 test cases + navigation_blocked event |
| Screenshot → artifact integration | Content-addressed storage | NFR-B3, metadata-only response |
| Tier-3 JS execution | `browser.evaluate` approval-gated | P4-I2, CapabilityDenied enforced |
| Browser event spine | 6 event types at schema 1.1.0 | Cardinality baseline 66 |
| Fleet separability | S-10 (9th optional member) | Spawned/absent both tested |
| G-SEC-2 | Fully closed | Both MCP-subprocess and claude-agent halves |
| ADR-0013 / ADR-0014 | Accepted | Transport + gate |

### Gaps Remaining for Phase 5

1. **Output caps for subprocess-based MCP executors** — `run_git` and `run_recipe` use unbounded `communicate()`; OOM risk on pathological output. Must land before any tool exposes raw blob content.
2. **Navigation tool deduplication** — 150 lines of near-duplicate code in the browser tool surface. Refactoring to `_forward_action_tool` reduces maintenance burden and origin-check consistency risk.
3. **Tool naming gate** — `browser_take_screenshot` underscore deviation proves code review alone is insufficient for naming conventions. An AST gate would catch this at CI time.
4. **Docker-in-Docker CI support** — P4-I1 ephemerality, container cleanup, and screenshot artifact round-trip tests all skip without Docker-in-Docker in CI. The coverage gap is structural, not test-quality.
5. **Digest freshness gate** — The format-only gate is the floor; a content-correct gate (pinned canonical digest verified against upstream) is the ceiling.
6. **Fleet-level integration test** — No multi-server workflow test (e.g., "orchestrator calls git + verification + browser in sequence") exists. Each server is tested in isolation. This is acceptable for Phase 4 but must close before production orchestration.

### Verdict

Phase 4 delivered the browser automation plane on a stable fleet foundation, proving the ADR-0010 recipe scales to a 4th archetype (Docker-in-Docker). The three epics shipped 17 stories, 15 browser tools, 6 event types, and 3 new security assertions under green gates. The G-SEC-2 closure is the highest-impact carry-forward resolution. The primary risks for Phase 5 are: (a) the output-cap gap in subprocess-based executors (OOM risk), (b) the naming-convention gap (code review is insufficient), and (c) the Docker-in-Docker CI gap that leaves P4-I1 partially unproven at runtime. Phase 5's multi-runtime scope will exercise the fleet's composability for the first time — the fleet-level integration test gap must close before that.

---

## Frontmatter
- **Phase:** 4 (Browser Automation Plane)
- **Epics:** 20–22 (3 epics, 17 stories)
- **Timeline:** 2026-06-05 – 2026-06-06
- **Date:** 2026-06-06 (retro synthesis)
- **Author:** R2d2 + Claude
- **Defining outcome:** browser automation plane shipped on a proven recipe as the fleet's 4th archetype; G-SEC-2 fully closed; the forwarding pattern, origin control, and container sandbox are the process assets Phase 5 inherits.
