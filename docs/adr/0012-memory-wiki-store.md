---
id: ADR-0012
status: accepted
date: 2026-06-03
supersedes: null
---

# ADR-0012: Memory/wiki store — SQLite FTS5, own DB file, registry-DB isolation

## Status

**Accepted** — 2026-06-04. Accepted alongside the Phase-3 gate ([ADR-0009](./0009-phase-3-gate.md)); must be `accepted` before **Epic 18**'s first story merges (per ADR-0009 §3). Gates Epic 18 (`memory`/`wiki` MCP server, FR75).

## Context

FR75 ([`prd.md`](../../_bmad-output/planning-artifacts/prd.md) §"Phase 3 Scope Extension") requires a persistent cross-task knowledge store backed by the filesystem + SQLite FTS5, exposing `read`/`search`/`write` tools, that is **single-writer-safe** — it owns its own store file and **never** writes the registry DB (FR26 preserved).

The existing platform already runs SQLite as the registry-state store (opened `read_only=True` by the read-only MCP servers — `mcp-servers/task-registry/.../app/main.py:84`) and replicates its WAL via litestream (Epic 13, ADR-0007). The memory store must be a *separate* SQLite database so it shares none of that lifecycle: not the registry schema, not the single-writer (registry-state), not the litestream replication target. This is exactly the P3-I2 invariant.

## Decision

1. **A dedicated SQLite database file** at the memory-mcp's own subtree of the existing `oh-my-bmad-data` named volume (e.g. `oh-my-bmad-data/memory-mcp/store.db`). It is **not** the registry DB and shares no engine, connection pool, or schema with `registry-state`.

2. **FTS5 full-text search.** A `documents` table + an FTS5 virtual table for `search`. `write` upserts a document + its FTS index entry; `search` runs an FTS5 `MATCH` query; `read` fetches by key. FTS5 is a built-in SQLite extension — no new external dependency (consistent with the artifact store's simplicity stance, ADR-0011).

3. **Single-writer-safe by construction (FR26 / P3-I2).** Exactly one memory-mcp process is spawned (by the worker/orchestrator MCP-client group); it is the sole writer of its own DB. The database is opened in WAL mode for crash-safety. Because there is only ever one writer of this file, the single-writer guarantee holds without coordination — and it is a *different* file from the registry DB, so FR26 (registry-state is the sole writer of *persisted task/session state*) is untouched.

4. **Tiering (P3-I1).** `read`/`search` are Tier-1; `write` is Tier-2. No Tier-3 tool in the base FR75 surface (a future `delete`/`forget` tool, if added, would be Tier-3-gated).

5. **Spine observability without a second writer.** Each `write` emits a `memory.written` event through the FR26 writer (clawhip-bridge) with `trace_id`; the *content* lives only in the memory-mcp's own DB. `metrics-subscriber` derives `memory.*` metrics from those spine events under the bounded-cardinality discipline.

6. **No litestream replication.** The memory DB is regenerable knowledge, not authoritative platform state; like the artifact store (ADR-0011) it is deliberately outside the Epic-13 litestream WAL target. Operator-documented durability boundary.

7. **WAL file mode discipline.** Per the cross-uid group-write systemic umask gap memory, the memory-mcp DB + its `-wal`/`-shm` siblings MUST be created group-writable (umask 002 / explicit 0o660) so a cross-uid recovery path does not crash-loop — fold the umask-002 fix into Epic 18's store-init, do not point-fix file modes after the fact.

## Consequences

- **FR26 stays trivially true** — the memory DB is a different file with a different sole writer; the registry single-writer invariant is never in scope.
- **Separability S-8** toggles the memory-mcp spawn command; with it absent, the worker completes a scripted task that does not call memory tools (NFR-M8).
- **Search quality is FTS5-bounded** — adequate for cross-task recall; a future semantic/embedding layer is a separate, value-gated decision (not Phase 3).
- **The wiki skill precedent applies at the product level only** — this is a worker-facing MCP store, distinct from any operator-facing wiki; no shared backing store.

## Alternatives considered

- **Reuse the registry DB with a `memory` table.** Rejected — makes memory-mcp a second writer of the registry DB (violates FR26 + P3-I2) and entangles regenerable knowledge with litestream-replicated authoritative state.
- **Flat-file markdown + grep search.** Rejected — `search` would be O(n) scans with no ranking; FTS5 is built-in, indexed, and ranked at zero dependency cost.
- **External vector DB.** Rejected — new external dependency + new MCP-subprocess credential, against `prd.md:557` and the `_ENV_ALLOWLIST` discipline. Revisit only if FTS5 recall proves inadequate (value-gated, post-Phase-3).

## Linked artifacts

- [`prd.md`](../../_bmad-output/planning-artifacts/prd.md) §"Phase 3 Scope Extension" — FR75.
- [`architecture.md`](../../_bmad-output/planning-artifacts/architecture.md) §"Phase 3 Architecture Extension" — P3-I2 + Epic-18 wiring.
- [`phase-3-plan.md`](../../_bmad-output/planning-artifacts/phase-3-plan.md) §3 (Epic 18).
- [ADR-0010](./0010-mcp-server-authoring.md) — the authoring recipe Epic 18 follows.
- [ADR-0007](./0007-litestream-wal-replication.md) — the litestream target boundary this store sits outside of.
- Precedent: `mcp-servers/task-registry/.../app/main.py:84` (separate read-only SQLite engine).

— *R2d2, 2026-06-03 (proposed; via the BMad Phase-3 planning chain). Accepted 2026-06-04 (alongside the Phase-3 gate).*
