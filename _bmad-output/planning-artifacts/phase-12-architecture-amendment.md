## Phase 12 Architecture Amendment — Historical Event Replay

> **Amendment added:** 2026-06-09.
>
> **Companion documents:**
> - PRD amendment: see [`phase-12-prd-amendment.md`](./phase-12-prd-amendment.md) (FR134–FR138).
> - Historical event replay ADR: see [`docs/adr/0024-historical-event-replay.md`](../../docs/adr/0024-historical-event-replay.md).
> - Prior amendment: [`phase-11-architecture-amendment.md`](./phase-11-architecture-amendment.md) (P11-I1 through P11-I3).

**Theme.** Historical event replay — point-in-time reconstruction of system state from the append-only event log. Phase 12 adds a replay engine that re-uses the existing materializer, replay API endpoints on registry-api, task history auditing, replay validation, and snapshot management. Default deployments gain audit and validation capabilities without any new operational infrastructure.

### Preserved invariants (Phase 1 through Phase 11 carry forward)

All prior invariants stand unchanged. As they apply to the new surface:

- **Single-writer (FR26, P2-I1).** Replay reads events; it does not write them. The event log remains append-only.
- **Event-driven state transitions (P6-I3).** Replay materializes from events using the same materializer. No direct state mutations.
- **Append-only event log (P1-I1).** Replay never modifies, deletes, or reorders events in the log.
- **Tier-enforced authz.** Replay endpoints require JWT auth. Same tier enforcement as existing registry-api endpoints.
- **Credential isolation (P5-I1, P6-I5).** Replay package does not access credentials or secrets.
- **Runtime adapter protocol (ADR-0015).** No changes. Replay is orthogonal to the adapter protocol.

### New invariants (Phase 12)

| # | Invariant | Why |
|---|-----------|-----|
| **P12-I1** | **Replay is read-only.** Replay produces an ephemeral snapshot that is never written to the live database. No mutation of live state. | Writing replayed state to the live database would corrupt it. Read-only is the only safe default for historical reconstruction. |
| **P12-I2** | **Replay preserves event ordering.** Events are processed in `emitted_at_monotonic_ns` order. No reordering, no parallelism within a single replay. | Deterministic replay requires strict ordering. The monotonic sequence number is the authoritative ordering key. |

### ADR-0024: Historical Event Replay

**Status:** Accepted — 2026-06-09.

**Decision:**
1. Point-in-time replay via sequence number or timestamp. Sequence numbers preferred for determinism.
2. Replay API on registry-api: `GET /v1/events/replay` and `GET /v1/tasks/{id}/history`.
3. Re-use existing materializer — same code path as startup materialization.
4. Snapshot-assisted replay for performance: periodic snapshots, replay forward from nearest snapshot.
5. Read-only replay: never writes to the live database.
6. Memory-bounded batch processing: configurable batch size (default 500), 256MB limit.

**Alternatives rejected:**
- CQRS read model — adds a separate projection to maintain.
- Full database snapshots — write amplification on every event.
- Time-travel database (Dolt, LiteFS branching) — new database engine dependency.
- Client-side replay — trusts client to implement materializer correctly.
- Mutable replay (temp database) — persistence and cleanup concerns.

**Consequences:**
- Positive: audit capability, materializer validation, split-deployment state rebuild, deterministic.
- Negative: O(N) replay without snapshots, snapshot storage, API surface expansion, materializer coupling (replay reproduces materializer bugs).

### New package: `packages/replay/`

```
┌─────────────────────────────────────────────────────┐
│  packages/replay/                                   │
│                                                     │
│  ├── __init__.py                                    │
│  ├── engine.py                                      │
│  │   └── replay_events(up_to) -> ReplayResult       │
│  ├── snapshots.py                                   │
│  │   └── create_snapshot(), load_snapshot()         │
│  ├── validation.py                                  │
│  │   └── validate_replay() -> ValidationResult      │
│  ├── models.py                                      │
│  │   └── ReplayResult, ReplayMetadata, etc.         │
│  └── tests/                                         │
│      ├── test_engine.py                             │
│      ├── test_snapshots.py                          │
│      └── test_validation.py                         │
└─────────────────────────────────────────────────────┘
```

### Replay engine architecture

```
┌─────────────────────────────────────────────────────┐
│  Replay Request                                     │
│  (to_timestamp or to_sequence)                      │
│                    │                                │
│                    ▼                                │
│  ┌───────────────────────────────────────────┐     │
│  │ Snapshot lookup                           │     │
│  │ - Find most recent snapshot before target │     │
│  │ - If found: start from snapshot state     │     │
│  │ - If not found: start from empty state    │     │
│  └───────────────────────────────────────────┘     │
│                    │                                │
│                    ▼                                │
│  ┌───────────────────────────────────────────┐     │
│  │ Event stream reader                       │     │
│  │ - Read events from snapshot seq → target  │     │
│  │ - Batch size: 500 events                  │     │
│  │ - Memory check after each batch           │     │
│  └───────────────────────────────────────────┘     │
│                    │                                │
│                    ▼                                │
│  ┌───────────────────────────────────────────┐     │
│  │ Existing materializer (re-used)           │     │
│  │ - Same code path as registry-state startup│     │
│  │ - Produces materialized state             │     │
│  └───────────────────────────────────────────┘     │
│                    │                                │
│                    ▼                                │
│  ┌───────────────────────────────────────────┐     │
│  │ ReplayResult (read-only)                  │     │
│  │ - state snapshot (tasks, sessions, etc.)  │     │
│  │ - metadata (event count, duration)        │     │
│  │ - NEVER written to live DB                │     │
│  └───────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────┘
```

### Replay API integration

```python
# registry-api route handler
@router.get("/v1/events/replay")
async def replay_events(
    to_timestamp: datetime | None = None,
    to_sequence: int | None = None,
    actor: Actor = Depends(require_auth),
):
    # Mutually exclusive params
    if (to_timestamp is None) == (to_sequence is None):
        raise HTTPException(400, "Provide exactly one of to_timestamp, to_sequence")

    # Audit log (NFR-S17)
    logger.info("replay_requested", actor_id=actor.id, ...)

    result = await replay_events(up_to=to_timestamp or to_sequence)

    return ReplayResponse(
        state=result.state,
        metadata=result.metadata,
    )
```

### Task history query pattern

```python
# registry-api route handler
@router.get("/v1/tasks/{task_id}/history")
async def task_history(
    task_id: str,
    limit: int = 100,
    offset: int = 0,
    actor: Actor = Depends(require_auth),
):
    events = await read_task_events(task_id, limit=limit, offset=offset)
    # Each event includes: sequence_number, timestamp, event_type,
    # actor_id, trace_id, state_diff
    return TaskHistoryResponse(events=events, total=len(events))
```

### Per-epic wiring decisions

**Epic 60 — Replay engine.** `packages/replay/` provides `replay_events()`, snapshot management, and batch processing. Re-uses existing materializer from `registry-state`. Memory-bounded, read-only, deterministic.

**Epic 61 — Replay API + task history.** Two new endpoints on registry-api. Both require JWT auth. Replay endpoint delegates to `packages/replay/`. Task history reads events directly from the event log (no materializer needed — just filtering).

**Epic 62 — Replay validation + snapshot management.** Validation compares replayed state vs. live materialized state. Snapshot creation stores periodic checkpoints for fast replay. Both exposed via API and usable from CLI.

**Epic 63 — CI gates + hygiene + retrospective.** Fold Phase 12 into the existing discipline gate structure (fold 52-3 pattern). Full CI validation. Phase 12 retrospective.

### Component inventory changes

| Component | Workspace member | Role | New in Phase 12 |
|-----------|-----------------|------|-----------------|
| `packages/replay/` | New | Replay engine + snapshots + validation | Yes |
| `services/registry-api/` | Existing | New replay + history endpoints | Modified |
| `services/registry-state/` | Existing | Materializer re-used (not modified) | No change |
| Event log (JSONL) | Existing | Source of truth (read-only access) | No change |

### Phase 12 CI-gate additions

The PR-required-checks list expands per epic:

- **Epic 60:** `packages/replay/` imports cleanly. `replay_events()` produces deterministic results. Memory bounded under 256MB for 10K events. Read-only (no write-path imports).
- **Epic 61:** Replay API returns correct state. Task history returns correct event sequence. Auth enforced (401 without JWT). Mutual exclusion validated (400 with both params).
- **Epic 62:** Replay validation detects injected discrepancies. Snapshot creation and restoration works. Snapshot-assisted replay is faster than full replay.
- **Epic 63:** All discipline scripts exit 0. All existing tests pass. Phase 12 retrospective produced.

### Acceptance checklist

- [ ] Architecture amendment (this section) accepted; P12-I1 and P12-I2 invariants explicitly stated.
- [ ] ADR-0024 (`docs/adr/0024-historical-event-replay.md`) accepted.
- [ ] PRD amendment (FR134-FR138) accepted.
- [ ] `bmad-create-epics-and-stories` has decomposed the scope into Epics 60-63 stories.
- [ ] Each Phase 12 epic has its `phase: 12` label set in `sprint-status.yaml`.
- [ ] Replay never writes to live database (P12-I1 verified by test).
- [ ] Phase 12 retrospective produced.

— *Amendment by R2d2, 2026-06-09, via the BMad bmad-create-architecture workflow (amendment mode).*
