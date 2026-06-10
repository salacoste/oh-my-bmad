# Phase 12 Scope Extension — Historical Event Replay

> **Status:** Phase-12 PRD amendment. Adds point-in-time replay of the event log, task history auditing, replay validation, and snapshot management. FR/NFR numbering continues the canonical series (FR134 → FR138; NFR-O21; NFR-M12; NFR-R17; NFR-S17). Epic numbering starts at 60.
>
> **Selected via:** ADR-0024 accepted 2026-06-09. Architecture document's "Future work beyond Phase 11" lists historical event replay as a deferred item.

**Theme:** the **historical event replay** — point-in-time reconstruction of system state from the append-only event log, enabling auditing, materializer validation, and state rebuild on new nodes.

**Resolved scope (from architecture document future-work list + ADR-0024):**

- **IN.** Point-in-time replay engine (`packages/replay/`)
- **IN.** Replay API surface on registry-api (`GET /v1/events/replay`, `GET /v1/tasks/{id}/history`)
- **IN.** Replay validation tool (compare replayed vs. live materialized state)
- **IN.** Snapshot management (periodic snapshots for fast replay)
- **IN.** Memory-bounded batch processing
- **OUT.** Full CQRS read model, time-travel database, mutable replay, client-side replay

**Preserved invariants (carry from Phases 1–11 — non-negotiable):**

- **All prior invariants stand unchanged (P1-I1 through P11-I3).** Phase 12 adds read-only replay; it does not alter existing invariants.
- **Single-writer (FR26) unchanged.** Replay reads events; it does not write them.
- **Event-only state transitions (P6-I3) unchanged.** Replay materializes from events; no direct state mutations.
- **Append-only event log (P1-I1) unchanged.** Replay never modifies the event log.
- **Tier-enforced authz unchanged.** Replay endpoints require JWT auth; same tier enforcement.

---

## Phase 12 Functional Requirements

### Alpha — Replay engine (Epic 60)

- **FR134.** Point-in-time replay engine. New `packages/replay/` package provides `replay_events(up_to: datetime | int) -> ReplayResult` that reads the event log, materializes state up to the specified point (timestamp or sequence number), and returns a read-only snapshot. Re-uses the existing materializer from `registry-state`. Events are processed in `emitted_at_monotonic_ns` order. When `up_to` is a datetime, it maps to the last sequence number at or before that timestamp.

  **Acceptance criteria:**
  - `replay_events(up_to=5000)` returns state materialized from events 1–5000
  - `replay_events(up_to=datetime(...))` maps timestamp to sequence number and replays
  - Uses the same materializer as `registry-state` startup
  - Events processed in `emitted_at_monotonic_ns` order (P12-I2)
  - Returns `ReplayResult` with snapshot, event count, and replay duration
  - Never writes to the live database (P12-I1)
  - Memory-bounded: raises `ReplayMemoryError` if 256MB exceeded

### Beta — Replay API + task history (Epic 61)

- **FR135.** Replay API surface. `GET /v1/events/replay` endpoint on registry-api accepts `to_timestamp` (ISO 8601) or `to_sequence` (int) parameter (mutually exclusive). Returns materialized state at that point as JSON. Requires JWT auth. Response includes: tasks, sessions, workers, event count, replay duration.

  **Acceptance criteria:**
  - `GET /v1/events/replay?to_sequence=5000` returns state at event 5000
  - `GET /v1/events/replay?to_timestamp=2026-06-09T12:00:00Z` returns state at that time
  - Both parameters provided → 400 error
  - Neither parameter provided → 400 error
  - Requires JWT auth (401 if missing)
  - Response includes replay metadata (event count, duration, snapshot source)
  - Replay latency <5 seconds for 10K events (NFR-O21)

- **FR136.** Task history endpoint. `GET /v1/tasks/{id}/history` returns the sequence of events that affected a specific task. Each entry includes: `sequence_number`, `emitted_at_monotonic_ns`, `event_type`, `actor_id`, `trace_id`, and a summary of state changes (field-level diff from previous state). Results are ordered by sequence number.

  **Acceptance criteria:**
  - Returns events for the specified task ordered by sequence number
  - Each entry includes timestamp, event type, actor, and state diff
  - 404 if task ID not found in any event
  - Requires JWT auth
  - Supports pagination (`?limit=N&offset=M`)

### Gamma — Replay validation + snapshot management (Epic 62)

- **FR137.** Replay validation tool. CLI tool or API endpoint that compares replayed state vs. live materialized state, reporting discrepancies. Validates that the materializer produces the same result when replaying events as it does during normal operation. Reports: total state diff count, per-entity-type diff count, specific field mismatches.

  **Acceptance criteria:**
  - `GET /v1/events/replay/validate` compares replay-to-latest vs. live state
  - Returns diff summary: matching field count, mismatching field count, specific mismatches
  - Empty diff = materializer is consistent
  - Non-empty diff = actionable report of what diverged
  - Requires JWT auth

- **FR138.** Snapshot management. Explicit snapshot creation and management. `POST /v1/events/replay/snapshots` creates a snapshot at the current event log position. `GET /v1/events/replay/snapshots` lists existing snapshots with sequence number, timestamp, and size. Snapshots are stored as JSON in `REPLAY_SNAPSHOT_DIR`. Replay engine uses the most recent snapshot before the target point (Decision 4).

  **Acceptance criteria:**
  - Snapshot creation stores current materialized state with sequence number and timestamp
  - Snapshot list returns all stored snapshots sorted by sequence number
  - Replay uses most recent snapshot before target (skips already-materialized events)
  - Snapshots are JSON (same format as materialized state)
  - Configurable snapshot interval for automatic creation

## Phase 12 Non-Functional Requirements

- **NFR-O21 (Replay latency).** Point-in-time replay of 10K events completes in <5 seconds. Snapshot-assisted replay of 10K events after a snapshot completes in <1 second.
- **NFR-M12 (Read-only guarantee).** Replay never writes to the live database. Enforced at the API level (no write paths in replay endpoints) and at the engine level (ReplayResult is an immutable dataclass).
- **NFR-R17 (Memory bounded).** Replay uses configurable batch sizes (default 500 events). Never exceeds 256MB total memory. Raises `ReplayMemoryError` on limit breach rather than OOM.
- **NFR-S17 (Audit log).** Every replay operation is logged with: actor_id (from JWT), timestamp, target sequence/timestamp, event count processed, replay duration, and success/failure.

## Phase 12 Invariants

- **P12-I1: Replay is read-only.** No mutation of live state. Replay produces an ephemeral snapshot that is never written to the live database. Enforced by architecture (replay package has no database write imports).
- **P12-I2: Replay preserves event ordering.** Events are processed in `emitted_at_monotonic_ns` order. Sequence numbers are monotonically increasing. No reordering, no parallelism within a single replay.

## Phase 12 Architecture Decisions Required

- **ADR-0024: Historical event replay** — accepted 2026-06-09

## Phase 12 Ship-Blocker Checklist

1. [ ] All Phase 1–11 invariants regression-free
2. [ ] ADR-0024 accepted
3. [ ] `packages/replay/` imports cleanly, `replay_events()` works
4. [ ] Replay API endpoints return correct state
5. [ ] Task history endpoint returns correct event sequence
6. [ ] Replay validation detects injected discrepancies
7. [ ] Snapshot creation and restoration works
8. [ ] `just lint` EXIT 0
9. [ ] All discipline scripts exit 0
10. [ ] No new third-party dependencies
11. [ ] Replay never writes to live database (verified by test)
12. [ ] Memory bounded: test with 10K events stays under 256MB
13. [ ] All replay operations logged (NFR-S17)
14. [ ] Phase 12 retrospective produced

## Estimated Effort

**4 epics, ~8 stories, ~2-3 weeks solo-operator work.**

| Epic | Stories | Estimate |
|------|---------|----------|
| 60 — Replay engine | 2 | ~5 days |
| 61 — Replay API + task history | 2 | ~4 days |
| 62 — Replay validation + snapshot management | 2 | ~3 days |
| 63 — CI gates + hygiene + retrospective | 2 | ~2 days |

— *Amendment by R2d2, 2026-06-09, via the BMad planning workflow (Phase 12 scoping).*
