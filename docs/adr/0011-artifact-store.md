---
id: ADR-0011
status: proposed
date: 2026-06-03
supersedes: null
---

# ADR-0011: Artifact-store design — content-addressed local-FS store, FR26-safe

## Status

**Proposed** — 2026-06-03. Transitions to **accepted** before **Epic 19**'s first story merges (per [ADR-0009](./0009-phase-3-gate.md) §3). Gates Epic 19 (`artifact` MCP server + store, FR76).

## Context

FR76 ([`prd.md`](../../_bmad-output/planning-artifacts/prd.md) §"Phase 3 Scope Extension") requires a persisted build/run-output store — the "Artifact store — Phase 3" surface — exposing `put`/`get`/`list` MCP tools over a content-addressed backing store, with operator-configurable retention and **no new external dependency** (the project's simplicity principle, `prd.md:557`).

Two preserved invariants constrain the design hard:
- **FR26 single-writer (P2-I1).** The store must not become a second writer of the registry DB or the JSONL log. The precedent is `metrics-subscriber`'s cursor file (`architecture.md:1212`) — a service owning a private state file on the named volume, outside the registry DB.
- **P3-I2 (Phase 3 new invariant).** A server with a backing store owns an isolated file/subtree, single-writer by exactly that server process; spine *events* about store ops route through the FR26 writer, but store *content* is the server's private concern.

The plan flags that the backing store "may warrant its own infra sub-epic if it needs a new volume/sidecar" (`phase-3-plan.md:50`). This ADR resolves that: the own-subtree design needs no new infra.

## Decision

1. **Content-addressed, local filesystem.** `put(content)` computes `sha256(content)` and writes to `<root>/objects/<hash[:2]>/<hash>` under the artifact-mcp's own subtree of the existing `oh-my-bmad-data` named volume (e.g. `oh-my-bmad-data/artifact-mcp/`). `get(hash)` / `list()` read it. Content-addressing gives free dedup + integrity verification (re-hash on read).

2. **Logical-name index in the same subtree.** A small index (SQLite or a JSON manifest at `<root>/index`) maps operator-supplied logical names + metadata (task_id, created_at, size, retention class) → content hash. This index is the artifact-mcp's **own** store file (P3-I2) — never the registry DB.

3. **FR26-safe (P3-I2).** The store is opened single-writer by the single spawned artifact-mcp process. No registry-DB writes; no second JSONL writer. Every store operation additionally emits an `artifact.*` spine event (`artifact.put`/`artifact.deleted`) **through the FR26 writer** (clawhip-bridge) with `trace_id`, so the operation is observable on the spine and derivable by `metrics-subscriber` — but the bytes themselves live only in the content store.

4. **Tiering (P3-I1).** `get`/`list` are Tier-1; `put` is Tier-2; `delete` (if exposed) is Tier-3-gated through the approval flow with a negative test proving denial without `approval.granted`.

5. **Retention.** Operator-configurable TTL and/or total-size cap, read from env at startup (added to the child-env allowlist). Enforced by a sweep at server startup and after each `put` (delete the oldest objects past the cap; emit `artifact.deleted` events for each). Retention is the only deletion path that runs without a Tier-3 approval — it is system-initiated, not actor-initiated, and is bounded by the operator-set policy.

6. **No new external dependency.** Local FS only (plus stdlib `hashlib` + optionally stdlib `sqlite3` for the index). No object store, no sidecar container, no new volume — it reuses the existing `oh-my-bmad-data` named volume via its own subtree.

## Consequences

- **No infra sub-epic needed** (resolves the `phase-3-plan.md:50` open question) — the own-subtree design rides the existing named volume.
- **Crash-safety:** content writes are write-to-temp-then-atomic-rename within the subtree; a crashed `put` leaves an orphan temp file (swept on startup), never a corrupt object (content-addressing makes partial writes detectable on read by hash mismatch).
- **The store is litestream-irrelevant.** Epic 13's litestream replicates only the registry DB WAL; the artifact content store is regenerable build output, not authoritative state, so it is deliberately **not** replicated. This is an operator-documented durability boundary.
- **Separability S-9** toggles the artifact-mcp spawn command; with it absent, the worker completes a scripted task that does not call artifact tools (proving optionality, NFR-M8).

## Alternatives considered

- **Store artifacts in the registry DB as BLOBs.** Rejected — violates FR26 (second writer) and P3-I2, and bloats the DB that litestream replicates with regenerable build output.
- **An external object store (S3/MinIO).** Rejected — adds an external dependency against `prd.md:557` simplicity, and a new credential into the MCP subprocess env (against the `_ENV_ALLOWLIST` discipline). The Phase-2 litestream remote (S3/B2/R2) is for DB replication, a different concern.
- **A new dedicated named volume + sidecar.** Rejected — the own-subtree-on-existing-volume design (P3-I2) achieves isolation without the infra cost the plan worried about.

## Linked artifacts

- [`prd.md`](../../_bmad-output/planning-artifacts/prd.md) §"Phase 3 Scope Extension" — FR76.
- [`architecture.md`](../../_bmad-output/planning-artifacts/architecture.md) §"Phase 3 Architecture Extension" — P3-I2 + Epic-19 wiring.
- [`phase-3-plan.md`](../../_bmad-output/planning-artifacts/phase-3-plan.md) §3 (Epic 19) + §"Artifact-store infra" note.
- [ADR-0010](./0010-mcp-server-authoring.md) — the authoring recipe Epic 19 follows.
- Precedent: `architecture.md:1212` (metrics-subscriber own cursor file).

— *R2d2, 2026-06-03 (proposed; via the BMad Phase-3 planning chain).*
