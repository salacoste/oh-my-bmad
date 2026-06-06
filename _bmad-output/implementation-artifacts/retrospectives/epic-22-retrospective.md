# Epic 22 Retrospective — Browser CI Hardening + Security Gates

**Delivered**: 22.1 (ephemerality), 22.2 (Tier-3 denial), 22.3 (container-spawn), 22.4 (CI gates), 22.5 (digest pinning -- partial)
**Status**: 5/5 stories done, 32 integration tests pass, 5 Docker-only skipped

---

## 1. Wrong Assumption

**"The CI gate scripts would need browser-specific modifications."**

The team assumed `check_tier_declarations.py` and `check_event_registry.py` would require browser-mcp-specific branches. In practice, both scripts discover browser tools automatically: `check_tier_declarations.py` globs `mcp-servers/*/src/**/handlers/tools.py` (line 377) and found browser's 15 tools without any code change; `check_event_registry.py` scans all `mcp-servers/**` for `emit_event(...)` calls and found the 6 `browser.*` event types by string literal matching against `EVENT_TYPES`. The only new script was `check_browser_image_digest.py` -- genuinely browser-specific (verifying `@sha256:` format in spawn commands). The wrong assumption led to over-budgeting Story 22.4 for CI gate plumbing when the existing gates already covered browser tools through structural discovery.

## 2. Single Process Change

**Ship security assertions as test files alongside production code, not as separate integration tests.**

The `_build_docker_command` security assertions (13 tests in `test_browser_container_spawn.py`) are the most valuable artifact of this epic. They test that `--no-sandbox`, `--network host`, `npx`, and tag-only image references are absent from the spawn command. These tests are in `tests/integration/` but they test a pure function (`_build_docker_command`) with zero I/O. They should have been unit tests in `mcp-servers/browser/src/browser_mcp/test_playwright_subprocess.py` alongside the existing subprocess tests. The lesson: security invariants on internal functions should be unit-tested at the module level, not gated behind integration test infrastructure. This would have caught the `--no-sandbox` absence assertion on every local test run, not just CI.

## 3. Deferred-Item Triage

| Priority | Item | Source | Rationale |
|----------|------|--------|-----------|
| P1 (carry) | Digest pinning is format-only, not runtime-verified | Story 22.5 partial | `check_browser_image_digest.py` verifies the `@sha256:` format appears in documentation and test files, but does NOT verify the pinned digest matches a real upstream manifest (the `--verify-remote` flag requires `BROWSER_MCP_PLAYWRIGHT_IMAGE` env var + crane/skopeo, neither available in CI). The gate proves the format is correct, not that the digest is current. |
| P2 (carry) | Real Docker ephemerality tests skipped | `test_browser_ephemerality.py:43-48` | Both `test_cookie_not_persistent_across_sessions` and `test_localstorage_not_persistent_across_sessions` skip with "Requires Playwright Docker image in CI". The structural assertion (`test_storage_capability_suppressed`) passes, but P4-I1 (zero state leakage) is only proven by the `--isolated` flag in the spawn command, not by an actual cross-session test. |
| P2 (carry) | `check_browser_image_digest.py` does not pin a specific digest | scripts/check_browser_image_digest.py:36-37 | The `_DIGEST_PATTERN` regex validates format, but no canonical digest is hardcoded for comparison. The script checks that `@sha256:` EXISTS, not that it matches a known-good value. A drifted or stale digest passes the gate silently. |
| P3 (monitor) | Metrics-subscriber cardinality ratchet for `browser.*` family | Story 22.4 | The ratchet was updated but grep for "browser" in `check_metrics_subscriber.py` returns no hits, suggesting the cardinality assertion may be in a generated/derived file. Verify the ratchet actually covers all 6 browser event types. |
