# Story 19.5 — Separability S-9 + supply-chain + Epic 19 + Phase 3 close-out (recipe steps 7, 8)

**Status:** done · **Date:** 2026-06-04 · **FR:** FR76; NFR-M8 (S-9); NFR-S12; P3-I2/I3
**Closes:** Epic 19 (χ artifact MCP server) — and **Phase 3** (the five-server fleet)

## Summary

Story 19.5 makes artifact-mcp the **optional 8th** stdio member, adds its REQUIRED + retention
vars to both `_ENV_ALLOWLIST` frozensets (byte-identical, all non-secret), proves both
separability states with an S-9 test (binary put→get round-trip + P3-I2 isolation), confirms
zero supply-chain growth, and **closes Phase 3**: all five fleet MCP servers are shipped on the
ADR-0010 recipe.

## Recipe step 5/8 — Child-env allowlist + Separability S-9 (NFR-M8)

- **`_ENV_ALLOWLIST`** (both spawners, **byte-identical**): add `ARTIFACT_MCP_STORE_PATH` /
  `ARTIFACT_MCP_ACTOR_KIND` / `ARTIFACT_MCP_ACTOR_ID` (required) + `ARTIFACT_MCP_RETENTION_MAX_BYTES`
  / `ARTIFACT_MCP_RETENTION_TTL_SECONDS` (optional operator policy). **All non-secret**: the store
  root is artifact-mcp's OWN content-store subtree (never the registry DB — P3-I2); the retention
  vars are numeric policy. No external credential → no scoped token. Authored in the main context
  (the a0ca050 P0 area).
- **`MCPClientGroup`**: optional `artifact` member + conditional spawn gated on a non-blank
  `settings.artifact_command` (default `""` → OFF), nulled on exit.
- **`tests/separability/test_s9_artifact_optional.py`** (`@pytest.mark.slow`, no Docker):
  - **SPAWNED**: `artifact_command` set + `ARTIFACT_MCP_*` env → `MCPClientGroup` boots a **real
    artifact-mcp stdio subprocess** as the 8th member; `get`/`list`/`put`/`delete` appear in
    `list_tools()`; and an **`artifact.put` → `artifact.get` round-trip** works end-to-end through
    stdio with **arbitrary binary content** (all 256 byte values, base64 over the wire) recovered
    byte-identically — validating the 19.3 binary-safety fix in a real subprocess. **P3-I2
    asserted**: the put lands in artifact-mcp's OWN store root; the registry DB path is never
    created.
  - **ABSENT**: `artifact_command` blank → the 3 core MCP members still initialize
    (`clients.artifact is None`) and a scripted task completes — artifact-mcp is optional (NFR-M8).
  - Both pass locally (2 passed, 3.65s).
- **Contract tests extended**: `_SPAWNER_REQUIRED_ENV_VARS += ARTIFACT_MCP_*` (the 3 required);
  `validate_caller_trace_id` byte-identity + runtime-message identity extended to artifact-mcp
  (now **8 servers** — the complete Phase-3 stdio fleet carries the identical helper).

## Recipe step 7 — Supply-chain transitive inclusion (NFR-S12)

artifact-mcp's only third-party dependency is `mcp` (already in the base SBOM). **Zero new
third-party deps** — the content store is stdlib `hashlib` + `sqlite3` (FTS-free; raw SQL, no
SQLAlchemy → no ORM dep). License gate `--self-test` = 11/11, 0 failures. No new `release.yml`
matrix row (built into the base via `Dockerfile.base`). **ADR-0011 accepted.**

## Epic 19 acceptance gate — roll-up

| Gate bullet | Status | Evidence |
|---|---|---|
| `put`/`get` round-trip by content hash; `delete` Tier-3-denied without approval | ✅ | 19.2 store + 19.3 tools (delete `check_tier_with_approval`); PR #56/#57 |
| Retention enforced (sweep at startup + on `put`); store isolated in its own subtree (P3-I2), never the registry DB | ✅ | 19.2 sweep + isolation test; 19.5 S-9 P3-I2 assert |
| `artifact.*` events (incl. retention `artifact.deleted`) with `trace_id`; registered + cardinality green | ✅ | 19.4 (two-location, 1.1.0); PR #58 |
| Separability S-9 green; `_ENV_ALLOWLIST`-mirror + `validate_caller_trace_id` extended to artifact-mcp | ✅ | 19.5; PR #59 |
| Base image passes `just verify-images`; ADR-0011 `accepted` | ✅ | artifact-mcp rides the base; ADR-0011 accepted |

## Phase 3 — COMPLETE

Phase 3 (FR72–FR76, ADR-0009 gate) shipped **five fleet MCP servers**, all built to the single
**ADR-0010 MCP-server-authoring recipe**, validating the recipe across **three archetypes**:

| Epic | Server | Archetype | Tiers | Separability |
|---|---|---|---|---|
| 15 | `git` | subprocess-sandbox (worktree) | 1/2/3 | S-5 |
| 16 | `github` | REST-client (scoped credential) | 1/3 | S-6 |
| 17 | `verification` | subprocess-sandbox (build/test) | 2 | S-7 |
| 18 | `memory` | own SQLite FTS5 store | 1/2 | S-8 |
| 19 | `artifact` | content-addressed FS store | 1/2/3 | S-9 |

Cross-cutting invariants held across all five: per-tool tiering (P3-I1, `check_tier_declarations`
gate), explicit `caller_trace_id` (byte-identical `validate_caller_trace_id` across 8 stdio
servers), FR26 single-writer (events route through clawhip-bridge; stores are own-file/own-subtree,
P3-I2), child-env allowlist (no broad secret ever forwarded; github's scoped token is the only
credential, narrowly-named), supply-chain inheritance (zero new `release.yml` matrix rows across
all five), and NFR-M8 separability (each server is an optional stdio member, spawn-toggle-gated).

### Phase-3 carve-outs (tracked in deferred-work.md)
- **G-SEC-2 remaining half** (16.5): the claude-agent spawn still forwards the broad `GITHUB_TOKEN`
  for `git push` (`claude_code_runner.py:89`) — MCP-subprocess half closed; agent half tracked.
- **github Phase-1 simulated writes** (16.4): flip `simulate=False` + validate one real write.
- **Per-server env scoping** (defense-in-depth): the shared allowlist forwards each server's vars
  to all children — acceptable, ADR-0010-sanctioned enhancement.

## Disposition

artifact-mcp is the **fifth and final** Phase-3 server, the **second store-backed** one
(content-addressed FS + sqlite index), and the second to ship a Tier-3 approval-gated tool
(`delete`, alongside git's `push`/`rebase` and github's writes). With it, **Phase 3 is complete**:
the fleet recipe (ADR-0010) is proven and reusable, and every fleet server is tiered,
trace-correlated, store-isolated, supply-chain-inherited, and separability-proven.
