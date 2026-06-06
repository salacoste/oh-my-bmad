# Epic 20 Retrospective — Browser MCP Server Scaffold + Fleet Integration

**Delivered**: 20.1 (server scaffold), 20.2 (Playwright subprocess lifecycle), 20.3 (clawhip-bridge audit wiring), 20.4 (origin control), 20.5 (resource limits), 20.6 (fleet separability S-10)
**Status**: 6/6 stories done

---

## 1. Wrong Assumption

**"We would need a novel architecture for browser subprocess management."**

The team expected browser-MCP to require a bespoke container orchestrator. In practice, `PlaywrightSubprocessManager` (`adapters/playwright_subprocess.py`) follows the exact same pattern as every other MCP stdio member: a dataclass holding per-task sessions, `asyncio.create_subprocess_exec` for spawn, and SIGTERM/SIGKILL escalation for teardown. The `ClawhipBridgeClient` reuse from task-registry/git-mcp was a direct copy with zero structural changes. The S-10 separability test (`tests/separability/test_s10_browser_optional.py`) proved the blank-command toggle IS the entire separability seam -- no special fleet wiring needed. The "novel architecture" assumption added speculative design time to Story 20.1 that could have been avoided by recognizing the pattern match earlier.

## 2. Single Process Change

**Require a "pattern match audit" checkpoint before any new MCP server epic enters design.**

The clawhip-bridge audit wiring (20.3), env allowlist (20.6), and lifespan lifecycle were all byte-for-byte pattern matches against existing servers. Each was independently designed and reviewed, consuming 2-3 days of review cycles that would collapse to hours with a mandatory "which existing server already does this?" checklist item at design review. The evidence: `_BROWSER_ENV_ALLOWLIST` mirrors the allowlist in `test_clawhip_client_env_allowlist_mirror` contract tests; `EmitterHolder` is structurally identical to the git-mcp copy; the lifespan teardown order (artifact client first, then clawhip, then `pw_manager.kill_all()`) follows the same `try/finally` nesting as artifact-mcp.

## 3. Deferred-Item Triage

| Priority | Item | Source | Rationale |
|----------|------|--------|-----------|
| P1 (carry) | Real Docker ephemerality tests (cookies/localStorage isolation) | Story 22.1 scaffold | Two tests in `test_browser_ephemerality.py` are `pytest.skip("Requires Playwright Docker image in CI")`. The structural assertions pass, but the actual cross-session state leak test is unvalidated. Block on CI Docker availability. |
| P2 (carry) | `browser_take_screenshot` naming inconsistency | tools.py:80 | All other tools use dotted names (`browser.navigate`, `browser.click`), but screenshot uses `browser_take_screenshot` (underscore, no dot). The TIER_MAP and `@mcp.tool(name=...)` both use the underscore form. Non-blocking but inconsistent with the ADR-0010 dotted-name convention. |
| P3 (monitor) | `_client_lock` per-manager, not per-task | playwright_subprocess.py:156 | A single `asyncio.Lock` serializes all `ensure_client()` calls across all tasks. Under concurrent multi-task load this becomes a bottleneck. Not observed in practice yet; monitor before splitting to per-task locks. |
