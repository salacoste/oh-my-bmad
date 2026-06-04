# Story 17.5 — Separability S-7 + supply-chain + Epic 17 close-out (recipe steps 7, 8)

**Status:** done · **Date:** 2026-06-04 · **FR:** FR74; NFR-M8 (S-7); NFR-S12; P3-I3
**Closes:** Epic 17 (υ verification MCP server — 3rd fleet server, reuses the ADR-0010 recipe)

## Summary

Story 17.5 makes verification-mcp an **optional** stdio member (the conditional 6th spawn,
mirroring git's 4th and github's 5th), adds its three **non-secret** REQUIRED vars to both
`_ENV_ALLOWLIST` frozensets, proves both separability states with an S-7 test, and confirms
zero supply-chain growth.

## Recipe step 5/8 — Child-env allowlist + Separability S-7 (NFR-M8)

- **`_ENV_ALLOWLIST`** (both worker-wrapper + orchestrator-adapter, **byte-identical**): add
  `VERIFICATION_MCP_WORKTREE_ROOT` / `VERIFICATION_MCP_ACTOR_KIND` / `VERIFICATION_MCP_ACTOR_ID`.
  **All three are NON-secret** (a worktree-root path + actor identity) — verification runs
  build/test recipes in the worktree sandbox and needs **no external credential**, so there
  is NO scoped-token entry (unlike github-mcp's `GITHUB_MCP_SCOPED_TOKEN`). The broad-secret
  denylist (`GITHUB_TOKEN` / `ANTHROPIC_API_KEY` / `OPERATOR_HMAC_KEY` / AWS / OpenAI) stays
  excluded. Authored in the main context (the a0ca050 P0 area) — because these vars carry no
  credential (git-style risk profile), no separate security-review lane was required (unlike
  16.5's scoped token).
- **`MCPClientGroup`**: optional `verification` member + conditional spawn gated on a
  non-blank `settings.verification_command` (default `""` → OFF), nulled on exit. Exact mirror
  of the git/github seams.
- **`tests/separability/test_s7_verification_optional.py`** (`@pytest.mark.slow`, no Docker):
  - **SPAWNED**: `verification_command` set + `VERIFICATION_MCP_*` env (a real worktree root)
    → `MCPClientGroup` boots a **real verification-mcp stdio subprocess** as the 6th member;
    `verification.run_build` + `verification.run_tests` appear in `list_tools()`; and
    `verification.run_build` is callable **end-to-end through the stdio boundary** — the
    default recipe in a justfile-less worktree returns a structured failure (`pass=False`)
    plus the `verification.completed` event descriptor, with **no live build toolchain** and
    **no secret** crossing into the recipe env.
  - **ABSENT**: `verification_command` blank → the 3 core MCP members still initialize
    (`clients.verification is None`) and a scripted `task_add_note` round-trip completes —
    verification-mcp is optional (NFR-M8).
  - Both pass locally (2 passed, 3.81s).
- **Contract tests extended**: `_SPAWNER_REQUIRED_ENV_VARS += VERIFICATION_MCP_*`;
  `validate_caller_trace_id` byte-identity + runtime-message identity extended to
  verification-mcp (now **6 servers**).

## Recipe step 7 — Supply-chain transitive inclusion (NFR-S12)

verification-mcp's dependencies resolved against `uv.lock`:

| Dependency | Kind | Already in base SBOM? |
|---|---|---|
| `capabilities`, `events` | internal workspace packages | n/a (first-party) |
| `mcp` | **third-party** | **yes** — dep of all existing stdio servers |

**verification-mcp introduces ZERO new third-party transitive dependencies** — its only
non-workspace dep, `mcp`, is already locked and carried in the base SBOM. (Unlike github-mcp,
it does not even pull aiohttp/tenacity — it runs subprocesses, not HTTP.)

- **No new `release.yml` matrix row** — the publish matrix is services-only; verification-mcp
  is a `mcp-servers/*` workspace member built into the base via `Dockerfile.base` `COPY
  mcp-servers/` + `uv sync --all-packages`. It is a stdio subprocess tool, not a network
  service (P3-I3).
- **License gate green** — `scripts/check_sbom_licenses.py --self-test` = 11/11, 0 failures.
- **`just verify-images`** verifies the base-image cosign signature; verification-mcp ships
  inside the existing base image (no new image), so the gate is structurally unaffected
  (formal exercise is release-time).

## Epic 17 acceptance gate — roll-up

| Gate bullet | Status | Evidence |
|---|---|---|
| Build/test recipes run sandboxed to the worktree (cwd-pinned, allowlist-only env, timeout-bounded) returning `{pass/fail, logs, coverage}` | ✅ | 17.2 `VerificationExecutor` + 17.3 tools; PR #48/#49 |
| `verification.*` events emitted with recipe-invoked + exit-status + `trace_id`; types registered + cardinality green | ✅ | 17.4 `verification.completed` (two-location, 1.1.0); PR #50 |
| Separability S-7 green (spawned + absent) | ✅ | 17.5 `test_s7_verification_optional.py` |
| `_ENV_ALLOWLIST`-mirror + `validate_caller_trace_id` contract tests extended to verification-mcp | ✅ | 17.5; PR #51 |
| Base image passes `just verify-images` | ✅ | verification-mcp rides the base image |

## Carve-out (none new)

verification-mcp carries no credential, so it has no G-SEC-2-style follow-up. The sandbox
(cwd-pin + secret-free env-allowlist + timeout + realpath containment + exec-not-shell) is the
complete containment for a server that executes project code — no deferred security item.

## Disposition

verification-mcp is the **third** server built to the ADR-0010 MCP-server-authoring recipe,
reusing it verbatim. As the closest sibling to git-mcp (sandboxed subprocess execution), it
further validates the recipe's reach across server archetypes (read/write API client →
github; subprocess sandbox → git + verification). No new ADR; no supply-chain matrix growth.
Epics 18–19 remain.
