---
id: ADR-0013
status: proposed
date: 2026-06-05
supersedes: null
---

# ADR-0013: Playwright MCP as browser transport

## Status

**Proposed** — 2026-06-05. Must be `accepted` before **Epic 20**'s first story merges to `main` (per the Phase-4 gate). Resolves the browser-automation surface decision deferred in [ADR-0009](./0009-phase-3-gate.md) §3.

## Context

ADR-0009 §3 explicitly deferred the browser-automation surface decision to Phase 4. The PRD and architecture documents referenced `browser-harness` (a raw Chrome DevTools Protocol fork) as the planned transport, but that reference was a placeholder — no implementation work had begun.

Phase 3's retrospective research evaluated two candidates for the browser automation MCP server (the project's 6th fleet member):

1. **`@playwright/mcp`** (Microsoft) — a maintained npm package exposing Playwright's browser automation as an MCP server over stdio, with built-in capability gating (`--caps`), session isolation (`--isolated`), origin control (`--allowed-origins` / `--allowed-hosts`), and an official Docker image (`mcr.microsoft.com/playwright/mcp`). Uses an accessibility-tree snapshot model (structured JSON) rather than raw screenshots.

2. **`browser-harness`** (raw CDP fork) — the originally-planned approach: fork a CDP library, maintain it as an upstream sync burden, and build the MCP tool surface from scratch over raw DevTools Protocol messages.

The research concluded that `@playwright/mcp` is the superior primary choice. `browser-harness` remains viable as a future Tier-3 escape hatch if low-level CDP control (network interception, raw protocol access) becomes necessary, but that need has not materialized and is not anticipated in Phase 4.

This ADR formalizes that transport decision. The server's authoring pattern (ADR-0010), tier mapping, event-spine integration, and container-sandboxing strategy are covered in the Phase-4 architecture amendment and are not duplicated here.

## Decision

1. **Use `@playwright/mcp` as the browser transport** — NOT `browser-harness`. The oh-my-bmad `browser` MCP server wraps the Playwright MCP subprocess; it does not speak CDP directly. Playwright MCP is consumed as a **versioned dependency** (Docker image pinned by digest), not as an upstream fork requiring sync.

2. **Playwright runs as a managed Docker subprocess** — `docker run -i --rm --init mcr.microsoft.com/playwright/mcp@sha256:<pinned-digest>` — not bare-metal via `npx`. This provides process-level sandboxing (seccomp, user namespaces) and network isolation (Docker bridge network). The `--no-sandbox` flag is **never passed**. The CI gate asserts the spawn command contains `docker run` and not `npx`.

3. **stdio transport only** — Playwright MCP is spawned with its default stdio transport (no `--port` / HTTP mode). The oh-my-bmad browser server communicates with it over stdin/stdout pipes, consistent with P2-I4 (MCP transport stdio-only).

4. **`--caps` flag provides dual enforcement.** Playwright's `--caps` flag suppresses entire capability groups at the subprocess level (defense-in-depth); oh-my-bmad's `TIER_MAP` enforces tier policy at the server level. A tool suppressed by `--caps` is unreachable even if the `TIER_MAP` entry were misconfigured. Default caps:

   | `--caps` value | Tier coverage | Tools available |
   |---|---|---|
   | `core` | Tier-0 (read-only) | `browser_snapshot`, `browser_click`, `browser_type`, `browser_select_option`, `browser_wait` |
   | `config` | Tier-1..2 (navigation + tab management) | `browser_navigate`, `browser_go_back`, `browser_go_forward`, `browser_tab_list`, `browser_tab_new`, `browser_tab_close`, `browser_take_screenshot` |
   | `evaluate` | Tier-3 (JS execution — approval-gated) | `browser_evaluate`, `browser_run_code` |

   Default is `--caps=core,config` (Tier-0 through Tier-2 only). The operator enables additional capabilities via `BROWSER_MCP_EXTRA_CAPS` (added to the child-env allowlist).

5. **`storage` and `network` caps are BLOCKLISTED** — never allowed, regardless of `BROWSER_MCP_EXTRA_CAPS`. The `storage` cap would expose `browser_set_storage_state` / `browser_storage_state` (cross-task credential leak — violates P4-I1). The `network` cap would expose `browser_network_requests` / `browser_network_response` (unrestricted network observation — unnecessary for automation and a surveillance surface). The blocklist is enforced in the browser server's startup validation, not just omitted from `--caps`.

6. **`--isolated` mode for session ephemerality (P4-I1).** Every Playwright subprocess is launched with `--isolated` (profile kept in memory, never persisted to disk). Combined with per-task subprocess respawn and the suppressed `storage` cap, this ensures no cookie/localStorage/sessionStorage state leaks between tasks.

7. **`--headless` always.** No display is available in the container; headless mode is mandatory, not configurable.

8. **Origin control via `--allowed-origins` / `--allowed-hosts`.** The operator configures permitted origins through `BROWSER_MCP_ALLOWED_ORIGINS` (child-env allowlist). If set, the Playwright subprocess rejects navigations to non-matching origins. If unset, all origins are permitted (operator's choice — no default restriction).

9. **Image pinned by digest.** The Docker image reference is `mcr.microsoft.com/playwright/mcp@sha256:<pinned-digest>` — not a floating tag. Digest rotation is an explicit operator action with a supply-chain verification step.

## Consequences

- **Positive: Microsoft-maintained, active community.** `@playwright/mcp` has ~33.5k GitHub stars, regular releases, and Microsoft's engineering team behind it. Bug fixes and browser-compatibility updates flow upstream without project effort.

- **Positive: Dependency, not fork.** No upstream-sync burden. The project consumes a versioned Docker image; updating is pulling a new digest, not rebasing a fork.

- **Positive: Accessibility-tree model.** Structured JSON snapshots (roles, refs, text content) are deterministic for LLM consumption — matching the project's "structured output over raw data" principle. No vision/screenshot dependency for core navigation.

- **Positive: Maps cleanly to the existing tier system.** Playwright's `--caps` groups align naturally with Tier-0..3. Dual enforcement (`--caps` + `TIER_MAP`) provides defense-in-depth.

- **Negative: Requires Docker-in-Docker for container-based spawning.** The browser server itself runs inside the base image (a container); spawning the Playwright MCP as `docker run` means a container spawning another container. This requires Docker socket or Docker-in-Docker setup in the deployment environment. The alternative (bare-metal `npx`) is rejected per P4-I3 (process sandboxing).

- **Negative: Less low-level control than raw CDP.** Network interception, raw protocol message inspection, and custom CDP sessions are not available through the Playwright MCP abstraction. These capabilities remain accessible via a future `browser-harness` escape hatch (Tier-3, post-Phase-4).

- **`browser-harness` remains a future Tier-3 escape hatch.** If a concrete need for raw CDP access materializes, the `browser-harness` approach can be revived as an alternative transport. This ADR does not preclude that; it selects the primary transport for Phase 4.

## Alternatives considered

- **`browser-harness` (raw CDP fork) as primary transport.** Rejected — introduces upstream-sync burden, lacks built-in capability gating, requires building the MCP tool surface from scratch over raw protocol messages, and provides no official Docker image. Maintained as a future escape hatch, not the primary transport.

- **Bare-metal `npx @playwright/mcp` (no Docker).** Rejected — violates P4-I3 (process sandboxing). A browser subprocess with access to `file://` URLs and local network scanning is a privileged process; running it bare-metal on the host means a compromised page can reach the host filesystem. Docker's default seccomp profile + user-namespace isolation limits the blast radius.

- **HTTP transport (`--port`) to Playwright MCP.** Rejected — violates P2-I4 (MCP transport stdio-only) and introduces a new network surface. The stdio transport is sufficient and consistent with every other fleet member.

- **Screenshots/vision as the primary interaction model.** Rejected — accessibility-tree snapshots are deterministic, structured, and token-efficient for LLM consumption. Screenshots remain available as an optional Tier-1 tool (`browser_take_screenshot`) for cases where visual verification is needed, but they are not the primary model.

## Linked artifacts

- [ADR-0009](./0009-phase-3-gate.md) §3 — the deferred browser-automation surface decision this ADR resolves.
- [ADR-0010](./0010-mcp-server-authoring.md) — the authoring recipe the browser server follows.
- [`phase-4-architecture-amendment.md`](../../_bmad-output/planning-artifacts/phase-4-architecture-amendment.md) — P4-I1/I2/I3 + Browser Worker archetype + tool surface mapping + security model.
- [`phase-4-prd-amendment.md`](../../_bmad-output/planning-artifacts/phase-4-prd-amendment.md) — FR78+FR87 + NFR-O12/M9/S13/R9.
- [`prd.md`](../../_bmad-output/planning-artifacts/prd.md) §"Phase 4 Scope Extension" — the browser-automation plane requirements.
- [`architecture.md`](../../_bmad-output/planning-artifacts/architecture.md) §"Phase 4 Architecture Extension" — preserved invariants + new invariants + archetype definition.

— *R2d2, 2026-06-05 (proposed; via the Phase-4 planning chain).*
