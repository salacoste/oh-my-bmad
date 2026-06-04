# Story 18.5 — Separability S-8 + supply-chain + Epic 18 close-out (recipe steps 7, 8)

**Status:** done · **Date:** 2026-06-04 · **FR:** FR75; NFR-M8 (S-8); NFR-S12; P3-I2/I3
**Closes:** Epic 18 (φ memory/wiki MCP server — 4th fleet server; ADR-0010 recipe + ADR-0012 store)

## Summary

Story 18.5 makes memory-mcp an **optional** stdio member (the conditional 7th spawn), adds
its three **non-secret** REQUIRED vars to both `_ENV_ALLOWLIST` frozensets, proves both
separability states with an S-8 test (including a real write→read round-trip + P3-I2
isolation), and confirms zero supply-chain growth. ADR-0012 (the store-design gate) is
`accepted`.

## Recipe step 5/8 — Child-env allowlist + Separability S-8 (NFR-M8)

- **`_ENV_ALLOWLIST`** (both spawners, **byte-identical**): add `MEMORY_MCP_STORE_PATH` /
  `MEMORY_MCP_ACTOR_KIND` / `MEMORY_MCP_ACTOR_ID`. **All non-secret**: `MEMORY_MCP_STORE_PATH`
  is the path to memory-mcp's OWN dedicated SQLite store (NEVER the registry DB — P3-I2) plus
  the actor identity. memory needs no external credential, so there is NO scoped-token entry.
  Authored in the main context (the a0ca050 P0 area); non-credential risk profile.
- **`MCPClientGroup`**: optional `memory` member + conditional spawn gated on a non-blank
  `settings.memory_command` (default `""` → OFF), nulled on exit. Mirror of the git/github/
  verification seams.
- **`tests/separability/test_s8_memory_optional.py`** (`@pytest.mark.slow`, no Docker):
  - **SPAWNED**: `memory_command` set + `MEMORY_MCP_*` env → `MCPClientGroup` boots a **real
    memory-mcp stdio subprocess** as the 7th member; `memory.read`/`memory.search`/`memory.write`
    appear in `list_tools()`; and a **`memory.write` → `memory.read` round-trip works end-to-end**
    through the stdio boundary (write a doc, read it back, `found=True`). **P3-I2 asserted**:
    the write lands in memory-mcp's OWN store file, and the sibling registry DB path is **never
    created** by a memory operation.
  - **ABSENT**: `memory_command` blank → the 3 core MCP members still initialize
    (`clients.memory is None`) and a scripted `task_add_note` round-trip completes — memory-mcp
    is optional (NFR-M8).
  - Both pass locally (2 passed, 2.96s).
- **Contract tests extended**: `_SPAWNER_REQUIRED_ENV_VARS += MEMORY_MCP_*`;
  `validate_caller_trace_id` byte-identity + runtime-message identity extended to memory-mcp
  (now **7 servers** — the full Phase-3 fleet of stdio MCP servers carrying the helper).

## Recipe step 7 — Supply-chain transitive inclusion (NFR-S12)

memory-mcp's dependencies resolved against `uv.lock`:

| Dependency | Kind | Already in base SBOM? |
|---|---|---|
| `capabilities`, `events` | internal workspace packages | n/a (first-party) |
| `mcp` | **third-party** | **yes** — dep of all existing stdio servers |

**memory-mcp introduces ZERO new third-party transitive dependencies** — its only non-workspace
dep is `mcp`. **FTS5 is built into the stdlib `sqlite3`** (ADR-0012 §2), so the full-text search
adds no dependency, and using raw `sqlite3` (not SQLAlchemy) means no ORM dep either.

- **No new `release.yml` matrix row** — the publish matrix is services-only; memory-mcp is a
  `mcp-servers/*` workspace member built into the base via `Dockerfile.base`.
- **License gate green** — `scripts/check_sbom_licenses.py --self-test` = 11/11, 0 failures.
- **`just verify-images`** verifies the base-image cosign signature; memory-mcp ships inside the
  existing base image.

## Recipe step 7 — ADR-0012 finalization

**ADR-0012** (`docs/adr/0012-memory-wiki-store.md`) is `status: accepted` (2026-06-04). All its
decisions are realized: own SQLite DB file (P3-I2); FTS5 search; single-writer-safe by
construction (one instance, WAL); Tier-1 read/search + Tier-2 write; `memory.written` through the
FR26 writer; no litestream; **0o660/0o2775 file-mode discipline** folded into store-init (§7).

## Epic 18 acceptance gate — roll-up

| Gate bullet | Status | Evidence |
|---|---|---|
| `search` returns ranked FTS5 results; read/write at Tier-1/Tier-2 | ✅ | 18.2 store + 18.3/18.4 tools; PR #52–#54 |
| Store isolated — own SQLite file, never the registry DB (P3-I2); single-writer-safe | ✅ | 18.2 store-isolation reference test + 18.5 S-8 P3-I2 assert |
| `memory.*` events emitted with `trace_id`; registered + cardinality green | ✅ | 18.4 `memory.written` (two-location, 1.1.0); PR #54 |
| Separability S-8 green (spawned + absent) | ✅ | 18.5 `test_s8_memory_optional.py` |
| `_ENV_ALLOWLIST`-mirror + `validate_caller_trace_id` contract tests extended to memory-mcp | ✅ | 18.5; PR #55 |
| Base image passes `just verify-images`; ADR-0012 `accepted` | ✅ | memory-mcp rides the base image; ADR-0012 accepted |

## Carve-out (none new)

memory-mcp carries no credential, so it has no G-SEC-2-style follow-up. The store-init folds the
0o660/0o2775 umask fix in from the start (ADR-0012 §7), so there is no deferred file-mode item.
A future `delete`/`forget` tool would be Tier-3-gated (ADR-0012 §4) — out of the base FR75 scope.

## Disposition

memory-mcp is the **fourth** server built to the ADR-0010 recipe and the **first store-backed**
one (gated additionally by ADR-0012). It validates the recipe's reach to a **new archetype** —
an own-SQLite-store server — alongside the subprocess-sandbox (git, verification) and REST-client
(github) archetypes. No supply-chain matrix growth. Only Epic 19 (`artifact` MCP server + store)
remains in Phase 3.
