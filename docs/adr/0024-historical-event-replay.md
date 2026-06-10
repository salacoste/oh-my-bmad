---
id: ADR-0024
status: accepted
date: 2026-06-09
supersedes: null
amends: null
---

# ADR-0024: Historical Event Replay

## Status

**Accepted** — 2026-06-09. Gates Phase 12 (historical event replay). Must be `accepted` before any code change that reads the event log for point-in-time state reconstruction.

## Context

The oh-my-bmad platform uses an append-only JSONL event spine as the source of truth (Phase 1, ADR-0001). The `registry-state` service materializes events into SQLite/Postgres for fast queries (Phase 6). The event log is schema-versioned (`schema_version` field on every envelope) and uses `emitted_at_monotonic_ns` for strict ordering.

The architecture document's "Future work beyond Phase 11" (`docs/architecture.md`) lists "Historical event replay" as a deferred item: "re-materialize state from the event log for auditing/debugging." Operators currently cannot:

1. Answer "what did the system state look like at time T?"
2. Replay events 100–200 to verify materializer correctness.
3. Rebuild state from scratch on a new node (needed for split deployment).
4. Audit task lifecycle historically.

The event log already exists. The materializer already exists. Phase 12 adds a replay engine that re-uses both, producing read-only snapshots at arbitrary points in time.

## Decision

### Decision 1 — Point-in-time replay via sequence number or timestamp

The replay engine accepts either an `emitted_at_monotonic_ns` timestamp or an event sequence number as the upper bound. It reads the event log from the beginning (or from the most recent snapshot before the target), applies events through the materializer up to and including the target point, and returns the resulting state as a read-only snapshot.

Sequence numbers are preferred for determinism (they are assigned at emission time and never change). Timestamps are accepted as a convenience, mapped to the last sequence number at or before the given timestamp.

### Decision 2 — Replay API surface on registry-api

Two new endpoints on `registry-api`:

- `GET /v1/events/replay?to_timestamp=<ISO8601>|to_sequence=<int>` — materializes state at the given point. Returns the full state snapshot (tasks, sessions, workers). Parameters are mutually exclusive.
- `GET /v1/tasks/{id}/history` — returns the sequence of events that affected a specific task, with `emitted_at_monotonic_ns`, `event_type`, `actor_id`, and a summary of state changes.

Both endpoints are read-only. Neither writes to the live database.

### Decision 3 — Re-use existing materializer

The same code that builds state from the event log on startup (in `registry-state`) is used for historical replay. No second materializer. The replay engine calls the materializer with a modified event source that stops at the target point.

Justification: A second materializer would diverge from the production code path, producing false confidence. Re-use ensures replayed state matches what the live system would have produced at that point.

### Decision 4 — Snapshot-assisted replay for performance

The replay engine creates and stores periodic state snapshots (configurable interval, default every 1000 events). When a replay targets a point after an existing snapshot, the engine starts from that snapshot and replays only the events after it, rather than replaying from the beginning.

Snapshots are stored as JSON files in a configurable directory (`REPLAY_SNAPSHOT_DIR`). Snapshot format is the same as the materialized state output — no new serialization format.

Justification: For a 10K-event log, replaying from scratch is acceptable (<5 seconds, NFR-O21). For a 100K+ event log, snapshot-assisted replay is necessary to meet the latency target. Snapshots are an optimization, not a correctness requirement — replay without snapshots always works.

### Decision 5 — Read-only replay, never mutates live state

Replay produces a read-only state snapshot in memory (or a temporary file). It never writes to the live SQLite/Postgres database. The returned snapshot is ephemeral unless the caller explicitly saves it.

Justification: Replaying events against the live database would corrupt state. Read-only is the only safe default for an operation that reconstructs historical state.

### Decision 6 — Memory-bounded batch processing

The replay engine processes events in configurable batch sizes (default 500 events). After each batch, it yields intermediate state and checks memory usage. If memory exceeds a configurable limit (default 256MB), it raises `ReplayMemoryError` rather than consuming unbounded memory.

Justification: Event logs grow without bound. Replaying a million events into a single in-memory state could exhaust memory. Batch processing with bounds prevents this.

## Consequences

### Positive

- **Audit capability.** Operators can inspect system state at any historical point. Task lifecycle becomes fully auditable.
- **Materializer validation.** Comparing replayed state vs. live state validates materializer correctness.
- **Split deployment enabler.** Rebuilding state from scratch on a new node is a replay from sequence 0.
- **No new persistence.** Re-uses the existing event log and materializer. Snapshots are an optional optimization.
- **Deterministic.** Sequence-number-based replay is fully deterministic.

### Negative

- **Replay latency grows with event count.** Without snapshots, replay is O(N) in event count. Mitigated by snapshot-assisted replay (Decision 4).
- **Snapshot storage.** Periodic snapshots consume disk space. Mitigated by configurable interval and cleanup policies.
- **API surface expansion.** Two new endpoints on registry-api increase the attack surface. Mitigated by read-only guarantee and existing authz (JWT required, tier-enforced).
- **Materializer coupling.** Replay correctness depends on the same materializer used for live state. If the materializer has a bug, replay reproduces the same bug. This is intentional (Decision 3) but means replay cannot detect materializer bugs independently — the validation tool (FR137) compares replay vs. live, not replay vs. ground truth.

## Alternatives considered

- **Event-sourced query model (CQRS read model).** Rejected — adds a separate read-model projection that must be maintained in parallel with the materializer. Replay re-uses the existing materializer instead.
- **Full database snapshots (SQLite backup at every state change).** Rejected — write amplification on every event. Event-level snapshots are cheaper and equally correct.
- **Time-travel database (Dolt, LiteFS branching).** Rejected — introduces a new database engine or filesystem dependency. The event log already captures history; replay just materializes it.
- **Client-side replay (download events, replay in browser/tool).** Rejected — trusts the client to implement the materializer correctly. Server-side replay uses the authoritative materializer.
- **Mutable replay (write to a temp database).** Rejected — even a temp database introduces persistence and cleanup concerns. In-memory snapshots are simpler and safer for the audit use case.

## Linked artifacts

- ADR-0001 — Event spine (the source of truth that replay reads).
- ADR-0017 — Postgres migration (registry-state materializes into Postgres in split deployment).
- ADR-0018 — Task state machine (replay reconstructs task state transitions).
- `docs/architecture.md` — "Future work beyond Phase 11" lists historical event replay.
- `services/registry-state/` — Existing materializer that replay re-uses.
- `services/registry-api/` — Host for the replay API endpoints.

— *R2d2, 2026-06-09.*
