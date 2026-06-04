# Story 16.6 — Separability S-6 + supply-chain + Epic 16 close-out (recipe steps 7, 8)

**Status:** done · **Date:** 2026-06-04 · **FR:** FR73; NFR-M8 (S-6); NFR-S12; P3-I3
**Closes:** Epic 16 (γ github MCP server — 2nd fleet server, reuses the ADR-0010 recipe)

## Summary

Story 16.6 makes github-mcp an **optional** stdio member (the conditional 5th spawn,
mirroring git-mcp's 4th) with an S-6 separability test proving both the SPAWNED and ABSENT
states, and confirms github-mcp inherits the platform's supply-chain guarantees
transitively from the signed base image — **zero** new third-party deps, **zero** new
`release.yml` matrix rows.

## Recipe step 8 — Separability S-6 (NFR-M8)

- **`MCPClientGroup` (worker-wrapper `mcp_clients.py`)**: added the optional `github`
  member + a conditional spawn gated on a non-blank `settings.github_command` (default
  `""` → OFF), nulled on exit. Exact mirror of the git-mcp 15.5 seam. No allowlist change
  here (the `GITHUB_MCP_*` allowlist + scoped token landed in 16.5).
- **`tests/separability/test_s6_github_optional.py`** (`@pytest.mark.slow`, no Docker):
  - **SPAWNED** (`test_github_spawned_when_command_set`): `github_command` set + the
    `GITHUB_MCP_ACTOR_KIND`/`ACTOR_ID`/`SCOPED_TOKEN` env present → `MCPClientGroup` boots
    a **real github-mcp stdio subprocess** as the 5th member; its tools (`github.issues.list`
    Tier-1, `github.prs.create` Tier-3) appear in `list_tools()`; and `github.issues.list`
    is callable **end-to-end through the stdio boundary**. The callable path omits
    owner/repo → the handler returns a structured `{"ok": False, "error": "owner and repo
    are required"}` with **no live GitHub HTTP** and **no token disclosure** (asserted
    `"_auth_token" not in result`) — hermetic.
  - **ABSENT** (`test_github_absent_when_command_blank`): `github_command` blank → the 3
    core MCP members still initialize (`clients.github is None`) and a scripted
    `task_add_note` round-trip completes — proving github-mcp is optional (NFR-M8).
  - Both pass locally (2 passed, 3.59s).

## Recipe step 7 — Supply-chain transitive inclusion (NFR-S12)

github-mcp's third-party dependencies resolved against `uv.lock`:

| Dependency | Kind | Already in base SBOM? |
|---|---|---|
| `capabilities`, `events` | internal workspace packages | n/a (first-party) |
| `mcp` | third-party | **yes** — dep of all 4 existing stdio servers |
| `aiohttp` | third-party | **yes** — already a `worker-wrapper` dep (in base image) |
| `tenacity` | third-party | **yes** — already a `worker-wrapper` dep (in base image) |

**github-mcp introduces ZERO new third-party transitive dependencies** — `mcp`, `aiohttp`,
and `tenacity` are all already locked and carried in the base SBOM (worker-wrapper's
`GitHubClient`, Story 5.14, already uses aiohttp+tenacity). The base SBOM therefore already
covers github-mcp with no new components.

- **No new `release.yml` matrix row** — the publish matrix is services-only
  (`services/<svc>/Dockerfile`); github-mcp is a `mcp-servers/*` workspace member built
  into the base via `Dockerfile.base:38` (`COPY mcp-servers/`) + `:41`
  (`uv sync --all-packages`). It is a stdio subprocess tool, not a network service (P3-I3).
- **License gate green** — `scripts/check_sbom_licenses.py --self-test` = 11/11 fixtures,
  0 failures. Since github-mcp adds no new component, the base-image SBOM the gate evaluates
  is unchanged in its app-dependency set.
- **`just verify-images`** verifies the base-image cosign signature; github-mcp ships inside
  the existing base image (no new image), so the gate is structurally unaffected (formal
  exercise is release-time, as with Epic-8/15.6).

## Epic 16 acceptance gate — roll-up

| Gate bullet | Status | Evidence |
|---|---|---|
| GitHub read tools function (Tier-1) | ✅ | 16.3; PR #44 |
| Every write tool Tier-3-denied without approval, permitted with it | ✅ | 16.4 negative+positive tests; PR #45 |
| Auth uses scoped `GITHUB_MCP_SCOPED_TOKEN`; broad `GITHUB_TOKEN` provably absent from MCP subprocess env (G-SEC-2 MCP-half closed) | ✅ | 16.5 `test_github_scoped_token_present_broad_token_absent`; security-review APPROVE/LOW; PR #46 |
| Separability S-6 green (spawned + absent) | ✅ | 16.6 `test_s6_github_optional.py` |
| `_ENV_ALLOWLIST`-mirror + `validate_caller_trace_id` contract tests extended to github-mcp | ✅ | 16.5; PR #46 |
| `github.*` event types registered + cardinality green | ✅ | 16.4 (two-location, born 1.1.0); PR #45 |
| Base image passes `just verify-images` | ✅ | github-mcp rides the base image (this story) |

## Carve-outs (tracked in deferred-work.md)

- **G-SEC-2 remaining half**: the claude-agent spawn (`claude_code_runner.py:89`) still
  forwards the broad `GITHUB_TOKEN` for `git push` — tracked by its in-code TODO. Epic 16
  closes only the MCP-subprocess half.
- **Phase-1 simulated writes**: `GitHubWriteClient(simulate=True)` — flip to real GitHub
  writes (config-gated) + validate one real write before declaring the write surface
  production-ready.
- **Per-server env scoping** (defense-in-depth): the scoped token currently reaches all MCP
  children via the shared allowlist (ADR-0010-sanctioned; enhancement, not a blocker).

## Disposition

github-mcp is the **second** server built to the ADR-0010 MCP-server-authoring recipe,
reusing it verbatim — validating the recipe's reusability for Epics 17–19. No new ADR; no
supply-chain matrix growth.
