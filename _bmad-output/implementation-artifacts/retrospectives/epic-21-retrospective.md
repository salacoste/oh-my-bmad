# Epic 21 Retrospective — Browser Tools + Event Spine Integration

**Delivered**: 21.1 (navigation Tier-1), 21.2 (interaction Tier-2), 21.3 (screenshot+artifact), 21.4 (evaluate Tier-3), 21.5 (tab management), 21.6 (event registration)
**Status**: 6/6 stories done, 15 tools shipped, 107 unit tests green

---

## 1. Wrong Assumption

**"Each browser tool needs its own handler implementation."**

Story 21.1 shipped three navigation tools with full inline handler bodies (80+ lines each for `browser_navigate`, `browser_navigate_back`, `browser_snapshot`). By Story 21.2 the team recognized the repetition and extracted `_forward_action_tool` (tools.py:514-573) -- a generic Tier-2 forwarder that reduced each of the six interaction tools to a 5-line wrapper: validate, check_tier, delegate to `_forward_action_tool`. Tab management tools (21.5) also used this helper. The result: 10 of 15 tools share a single forwarding path. The wrong assumption cost Story 21.1 ~200 lines of near-duplicate code that could have been deduplicated from the start. The `_forward_action_tool` pattern should have been the initial design for ALL non-navigate tools.

## 2. Single Process Change

**Extract the tool forwarding pattern into a reusable decorator BEFORE writing the first tool handler in a new server epic.**

The pattern -- validate caller_trace_id, check_tier, forward to subprocess, emit event, return structured response -- is now proven across 10 tools. The `_maybe_wrap` decorator (tools.py:292-306) for audit emission and the `_forward_action_tool` helper for Tier-2 forwarding are the two reusable abstractions. For any future MCP server that forwards to a subprocess (the expected pattern for all stdio-proxied servers), the team should start with these two primitives rather than re-deriving them per-tool. Evidence: the `_forward_action_tool` helper handles timing, error mapping, and event emission in a single place; adding `browser.tab_list` (Story 21.5) took 12 lines total.

## 3. Deferred-Item Triage

| Priority | Item | Source | Rationale |
|----------|------|--------|-----------|
| P1 (carry) | Navigate tools not using `_forward_action_tool` | tools.py:310-504 | `browser_navigate`, `browser_navigate_back`, and `browser_snapshot` still have full inline implementations with origin checking, result parsing, and event emission duplicated. These should be refactored to use the forwarding pattern with origin-check hooks. ~150 lines of deduplication. |
| P2 (carry) | `_parse_navigate_result` / `_parse_snapshot_result` fragile parsing | tools.py:183-262 | Result parsing from Playwright text output uses regex extraction (`_extract_field`). This is brittle against Playwright MCP output format changes. Should be replaced with structured JSON parsing once Playwright MCP supports it. |
| P3 (monitor) | `browser_take_screenshot` base64 decode in-band | tools.py:872-879 | Screenshot bytes are decoded from base64 in the tool handler, then re-hashed and re-encoded for artifact storage. For large screenshots this is a memory spike. Monitor for OOM in production; if observed, stream directly to artifact-mcp without decoding. |
| P4 (deprioritize) | `expression_hash` truncation in evaluate results | tools.py:998 | The full SHA-256 hash is returned; the result preview is truncated to 500 chars. No issue currently, but the hash cannot be reversed to verify against a known-good-expression registry. Not needed for Phase 4. |
