---
stepsCompleted:
  - step-01-validate-prerequisites
  - step-02-design-epics
  - step-03-create-stories
  - step-04-final-validation
workflowStatus: complete
finalStoryCount: 8
finalEpicCount: 4
inputDocuments:
  - _bmad-output/planning-artifacts/phase-12-prd-amendment.md
  - _bmad-output/planning-artifacts/phase-12-architecture-amendment.md
  - docs/adr/0024-historical-event-replay.md
workflowType: epics-and-stories
project_name: oh-my-bmad
user_name: R2d2
date: '2026-06-09'
---

# oh-my-bmad — Phase 12 Epic Breakdown: Historical Event Replay

## Overview

Phase 12 adds **historical event replay** — point-in-time reconstruction of system state from the append-only event log. This document decomposes FR134-FR138 and associated NFRs into **4 epics (60-63) and 8 stories**.

Source documents:
- PRD amendment: `_bmad-output/planning-artifacts/phase-12-prd-amendment.md` (FR134-FR138)
- Architecture amendment: `_bmad-output/planning-artifacts/phase-12-architecture-amendment.md`
- ADR-0024: `docs/adr/0024-historical-event-replay.md`

## Requirements Inventory

### Functional Requirements

**FR134.** Point-in-time replay engine: `packages/replay/` with `replay_events(up_to: datetime | int) -> ReplayResult`. Re-uses existing materializer. Read-only. Memory-bounded.

**FR135.** Replay API surface: `GET /v1/events/replay?to_timestamp=...|to_sequence=...`. Returns materialized state at that point. JWT auth required. Mutual exclusion of parameters.

**FR136.** Task history endpoint: `GET /v1/tasks/{id}/history`. Returns event sequence for a task with timestamps, actors, and state diffs. Pagination supported.

**FR137.** Replay validation tool: `GET /v1/events/replay/validate`. Compares replayed state vs. live materialized state. Reports discrepancies.

**FR138.** Snapshot management: `POST /v1/events/replay/snapshots` and `GET /v1/events/replay/snapshots`. Explicit snapshot creation and listing. Replay uses snapshots for fast restoration.

### Non-Functional Requirements

**NFR-O21.** Replay latency: 10K events in <5 seconds. Snapshot-assisted 10K events in <1 second.

**NFR-M12.** Read-only guarantee: replay never writes to the live database. Enforced at engine and API levels.

**NFR-R17.** Memory bounded: configurable batch sizes (default 500), 256MB limit, `ReplayMemoryError` on breach.

**NFR-S17.** Audit log: every replay operation logged with actor, timestamp, range, event count, duration, success/failure.

### FR Coverage Map

| FR | Epic | Story IDs | Notes |
|----|------|-----------|-------|
| FR134 | 60 | 60-1, 60-2 | Replay engine core |
| FR135 | 61 | 61-1 | Replay API endpoint |
| FR136 | 61 | 61-2 | Task history endpoint |
| FR137 | 62 | 62-1 | Replay validation |
| FR138 | 62 | 62-2 | Snapshot management |

**100% FR coverage confirmed — 5 FRs mapped across 4 epics, zero orphans.**

### NFR Coverage Map

| NFR | Epic | Story IDs | Notes |
|-----|------|-----------|-------|
| NFR-O21 | 60 | 60-1 | Replay latency benchmark |
| NFR-M12 | 60 | 60-1 | Read-only by design |
| NFR-R17 | 60 | 60-1 | Memory bounded |
| NFR-S17 | 61 | 61-1, 61-2 | Audit logging |

**100% NFR coverage confirmed — 4 NFRs mapped across 2 epics, zero orphans.**

## Epic List

### Dependency Graph

```
Epic 60 (Replay Engine) ──────► Epic 61 (Replay API + Task History)
                                        │
                                        ▼
                                 Epic 62 (Validation + Snapshots)
                                        │
                                        ▼
                                 Epic 63 (CI Gates + Retrospective)
```

### Standalone Value

- **Epic 60** delivers: `packages/replay/` with `replay_events()`, batch processing, memory bounds. Can be used programmatically without API.
- **Epic 61** delivers: HTTP API for replay and task history. Operators can query state at any point via curl.
- **Epic 62** delivers: Materializer validation (catches bugs) and snapshot management (performance).
- **Epic 63** delivers: CI integration, full validation, retrospective.

### Sequencing Rationale

Epic 60 is the foundation — the replay engine that all other epics depend on. Epic 61 wraps the engine in HTTP API. Epic 62 adds validation and optimization on top. Epic 63 lands last as the definitive validation gate.

## Epic 60: Replay Engine (backlog)

**Goal.** Provide the core replay engine in `packages/replay/`. `replay_events(up_to: datetime | int) -> ReplayResult` reads the event log, re-uses the existing materializer, processes events in `emitted_at_monotonic_ns` order, and returns a read-only state snapshot. Memory-bounded (256MB, configurable batch size). Read-only by design (no database write imports in the package).

**FRs covered:** FR134
**NFRs:** NFR-O21, NFR-M12, NFR-R17

### Story 60-1: Replay engine core

**Title:** Create packages/replay/ with replay_events()

**Description:** Create `packages/replay/` with the core replay engine. `replay_events(up_to)` reads the event log up to the specified point (sequence number or timestamp), applies events through the existing materializer, and returns a `ReplayResult` dataclass. Events processed in batches of 500. Memory checked after each batch. Returns `ReplayResult` containing: state snapshot (tasks, sessions, workers), event count, replay duration, sequence range. Never writes to the live database (P12-I1).

**Acceptance criteria:**
1. `replay_events(up_to=5000)` returns state materialized from events 1–5000.
2. `replay_events(up_to=datetime(...))` maps timestamp to sequence number and replays.
3. Events processed in `emitted_at_monotonic_ns` order (P12-I2).
4. Returns `ReplayResult` with snapshot, event count, duration.
5. Memory bounded: raises `ReplayMemoryError` if 256MB exceeded (NFR-R17).
6. Batch processing: configurable batch size (default 500).
7. No database write imports in the package (NFR-M12).
8. Uses the existing materializer from `registry-state`.

**Size:** L
**FR/NFR reference:** FR134, NFR-O21, NFR-M12, NFR-R17
**ATDD contracts:**
- Given 5000 events in the log, when `replay_events(up_to=5000)` is called, then state is materialized from events 1–5000 in sequence order.
- Given a timestamp mapping to sequence number 3000, when `replay_events(up_to=timestamp)` is called, then state is materialized from events 1–3000.
- Given an event log with 10K events, when `replay_events(up_to=10000)` is called, then it completes in <5 seconds (NFR-O21).
- Given a memory limit of 256MB and an event log that would exceed it, when replay runs, then `ReplayMemoryError` is raised rather than OOM.

### Story 60-2: Replay engine tests

**Title:** Unit + integration tests for replay engine

**Description:** Unit tests for the replay engine: deterministic replay, timestamp mapping, batch processing, memory bounds, read-only verification. Integration tests with a real event log fixture (1000+ events covering task lifecycle). Verify replay produces same state as live materializer.

**Acceptance criteria:**
1. >=15 unit tests covering: sequence replay, timestamp mapping, empty log, single event, batch boundary, memory limit.
2. Integration test with 1000-event fixture: replay produces same state as live materializer.
3. Test that replay never imports or calls any database write function.
4. Test batch processing produces identical results regardless of batch size.
5. Test `ReplayResult` serialization (JSON round-trip).

**Size:** M
**FR/NFR reference:** FR134
**ATDD contracts:**
- Given a 1000-event fixture, when replay is run, then the result matches live materialized state exactly.
- Given batch size 100 vs 500, when replay is run, then results are identical.
- Given `ReplayResult`, when serialized to JSON and back, then the result is equal.
- Given an empty event log, when replay is run, then an empty state snapshot is returned.

---

## Epic 61: Replay API + Task History (backlog)

**Goal.** Two new endpoints on registry-api: `GET /v1/events/replay` for point-in-time state reconstruction, and `GET /v1/tasks/{id}/history` for task lifecycle auditing. Both require JWT auth. Every replay operation is logged for audit (NFR-S17).

**FRs covered:** FR135, FR136
**NFRs:** NFR-S17

### Story 61-1: Replay API endpoint

**Title:** Add GET /v1/events/replay endpoint to registry-api

**Description:** Add `GET /v1/events/replay` endpoint to registry-api. Accepts `to_timestamp` (ISO 8601) or `to_sequence` (int), mutually exclusive. Delegates to `packages/replay/`. Returns materialized state as JSON with metadata. Requires JWT auth. Logs every replay operation (actor, timestamp, target, duration, success) for audit (NFR-S17).

**Acceptance criteria:**
1. `GET /v1/events/replay?to_sequence=5000` returns state at event 5000.
2. `GET /v1/events/replay?to_timestamp=2026-06-09T12:00:00Z` returns state at that time.
3. Both params → 400 error.
4. Neither param → 400 error.
5. No JWT → 401 error.
6. Response includes state, event count, replay duration, snapshot source.
7. Every request logged with actor_id, target, duration (NFR-S17).
8. Replay latency <5 seconds for 10K events (NFR-O21).

**Size:** M
**FR/NFR reference:** FR135, NFR-S17, NFR-O21
**ATDD contracts:**
- Given a valid JWT and `to_sequence=5000`, when the endpoint is called, then state at event 5000 is returned with 200.
- Given both `to_timestamp` and `to_sequence`, when the endpoint is called, then 400 is returned.
- Given no JWT, when the endpoint is called, then 401 is returned.
- Given a valid replay request, when the response is inspected, then audit log entry exists with actor_id and duration.

### Story 61-2: Task history endpoint

**Title:** Add GET /v1/tasks/{id}/history endpoint to registry-api

**Description:** Add `GET /v1/tasks/{id}/history` endpoint to registry-api. Returns events affecting the specified task, ordered by sequence number. Each entry includes: `sequence_number`, `emitted_at_monotonic_ns`, `event_type`, `actor_id`, `trace_id`, and a summary of state changes (field-level diff from previous state). Supports pagination (`?limit=N&offset=M`). Requires JWT auth. 404 if task ID not found.

**Acceptance criteria:**
1. Returns events for the task ordered by sequence number.
2. Each entry includes timestamp, event type, actor, trace_id, state diff.
3. 404 if task ID not found in any event.
4. Requires JWT auth (401 without).
5. Pagination works (`?limit=50&offset=100`).
6. Logged for audit (NFR-S17).

**Size:** M
**FR/NFR reference:** FR136, NFR-S17
**ATDD contracts:**
- Given a task with 5 events, when `GET /v1/tasks/{id}/history` is called, then 5 entries are returned in sequence order.
- Given a nonexistent task ID, when the endpoint is called, then 404 is returned.
- Given no JWT, when the endpoint is called, then 401 is returned.
- Given `?limit=2&offset=0`, when 5 events exist, then 2 entries are returned with a total count of 5.

---

## Epic 62: Replay Validation + Snapshot Management (backlog)

**Goal.** Replay validation compares replayed state vs. live materialized state, catching materializer bugs. Snapshot management enables fast replay by storing periodic checkpoints. Both exposed via API.

**FRs covered:** FR137, FR138

### Story 62-1: Replay validation tool

**Title:** Add replay validation endpoint and logic

**Description:** Add `GET /v1/events/replay/validate` endpoint that replays to the latest event and compares the result against the live materialized state. Reports: total field count, matching field count, mismatching field count, specific field mismatches with values. Empty diff = materializer is consistent. Non-empty diff = actionable report. Requires JWT auth.

**Acceptance criteria:**
1. Replays to latest event and compares against live state.
2. Returns diff summary: matching, mismatching, total fields.
3. Empty diff when replay matches live.
4. Specific field mismatches reported with expected vs actual.
5. Requires JWT auth.
6. Injected discrepancy (test) is detected and reported.

**Size:** M
**FR/NFR reference:** FR137
**ATDD contracts:**
- Given live state matching replay, when validate is called, then empty diff is returned.
- Given an injected discrepancy in live state, when validate is called, then the mismatching field is reported with expected and actual values.
- Given no JWT, when validate is called, then 401 is returned.

### Story 62-2: Snapshot management

**Title:** Add snapshot creation, listing, and restoration

**Description:** Add `POST /v1/events/replay/snapshots` (creates snapshot at current position) and `GET /v1/events/replay/snapshots` (lists existing snapshots). Snapshots stored as JSON in `REPLAY_SNAPSHOT_DIR`. Each snapshot records: sequence number, timestamp, state, size. Replay engine uses the most recent snapshot before the target point to skip already-materialized events.

**Acceptance criteria:**
1. `POST /v1/events/replay/snapshots` creates snapshot at current event log position.
2. `GET /v1/events/replay/snapshots` lists snapshots sorted by sequence number.
3. Each snapshot includes sequence number, timestamp, state, size.
4. Replay engine uses snapshot when available (skips to snapshot seq).
5. Snapshot-assisted replay faster than full replay for equivalent event count.
6. Requires JWT auth.

**Size:** M
**FR/NFR reference:** FR138
**ATDD contracts:**
- Given a snapshot at sequence 5000, when `replay_events(up_to=7000)` is called, then only events 5001–7000 are processed.
- Given no snapshots, when `replay_events(up_to=7000)` is called, then events 1–7000 are processed (full replay).
- Given `POST /v1/events/replay/snapshots`, when called, then a snapshot is created and appears in the list.
- Given snapshot-assisted replay vs full replay for 10K events after a snapshot, when durations are compared, then snapshot-assisted is faster.

---

## Epic 63: CI Gates + Hygiene + Retrospective (backlog)

**Goal.** Fold Phase 12 into the existing discipline gate structure. Full CI validation confirming all gates pass. Phase 12 retrospective documenting stories shipped, FR/NFR coverage, and carry-forward items.

### Story 63-1: Full CI validation

**Title:** Run all gates and verify Phase 12 invariants

**Description:** Run all gates: ruff (0 errors), mypy (0 errors), pytest (all pass), check_mcp_transport.py, check_single_writer.py, check_tier_declarations.py, check_imports.py. Verify P12-I1 (replay never writes to live DB) and P12-I2 (event ordering preserved). Verify NFR-M12 (read-only). Verify NFR-O21 (replay latency). Verify NFR-S17 (audit logging).

**Acceptance criteria:**
1. `just lint` EXIT 0.
2. All discipline scripts exit 0.
3. All tests pass (existing + new replay tests).
4. P12-I1 verified: no write-path imports in `packages/replay/`.
5. P12-I2 verified: replay processes events in monotonic order.
6. NFR-O21 verified: 10K event replay <5 seconds.
7. NFR-S17 verified: audit log entries for all replay operations.

**Size:** S
**FR/NFR reference:** NFR-M12, NFR-O21, NFR-R17, NFR-S17
**ATDD contracts:**
- Given `just lint`, when run, then exit code is 0.
- Given the `packages/replay/` source tree, when scanned for write-path imports, then none are found.
- Given a 10K event log, when replay runs, then it completes in <5 seconds.
- Given a replay request, when the audit log is inspected, then an entry exists with actor_id, target, and duration.

### Story 63-2: Phase 12 retrospective

**Title:** Produce Phase 12 retrospective

**Description:** Produce Phase 12 retrospective documenting: stories shipped, epics completed, FR/NFR coverage, carry-forward items (event log pruning, streaming replay for very large logs, replay with schema evolution), lessons learned. Update sprint-status.yaml with phase-12-complete audit trail.

**Acceptance criteria:**
1. Retrospective produced.
2. sprint-status.yaml updated.
3. FR/NFR coverage verified (all mapped).
4. Carry-forward items documented.
5. Phase 12 marked complete.

**Size:** S
**FR/NFR reference:** NFR-S17
**ATDD contracts:**
- Given the retrospective, when FR/NFR coverage is checked, then all 5 FRs and 4 NFRs are mapped to epics.
- Given sprint-status.yaml, when inspected, then phase-12-complete is recorded with audit trail.
- Given the retrospective, when carry-forward items are checked, then event log pruning, streaming replay, and schema evolution are listed.

---

## Cross-Epic Dependencies

| Dependency | Reason |
|------------|--------|
| Epic 60 (Engine) -> Epic 61 (API) | API delegates to replay engine |
| Epic 61 (API) -> Epic 62 (Validation) | Validation needs replay API working |
| Epic 62 (Validation) -> Epic 63 (CI) | CI gates validate all replay functionality |

## Requirements Coverage Matrix

| Requirement | Epic(s) | Fully Covered |
|-------------|---------|---------------|
| FR134 | Epic 60 | Yes |
| FR135 | Epic 61 | Yes |
| FR136 | Epic 61 | Yes |
| FR137 | Epic 62 | Yes |
| FR138 | Epic 62 | Yes |
| NFR-O21 | Epic 60 | Yes |
| NFR-M12 | Epic 60 | Yes |
| NFR-R17 | Epic 60 | Yes |
| NFR-S17 | Epic 61 | Yes |

**4 epics, 8 stories, FR134-FR138 + NFR-O21/M12/R17/S17 = 100% mapped, zero orphans.**
