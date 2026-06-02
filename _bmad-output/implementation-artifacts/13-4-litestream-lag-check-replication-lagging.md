# Story 13.4 — `just litestream-lag-check` + `replication.lagging` event (NFR-R7)

Status: review

<!-- Epic 13 capstone. Validation-first + subagent-orchestrated: two doc/explore
subagents verified the litestream metrics + the FR26 emit path BEFORE build; an
executor implemented; the orchestrator diff-audited and CAUGHT+FIXED a CRITICAL
cross-uid file-mode bug. -->

## Story

**As** the platform operator,
**I want** a `just litestream-lag-check` that detects a sustained replication
stall and emits a `replication.lagging` audit event (once per episode),
**so that** a silently-dead off-host replica surfaces instead of rotting.

## Verified design (subagent-corroborated, not guessed)

- **litestream metrics (0.3.x, source-verified):** enable via top-level
  `addr: ":9090"`; there is **no direct lag-seconds gauge**. The robust signal
  for any replica type is **`litestream_sync_count` STALLS** (normally ~1/s)
  **while `litestream_sync_error_count` RISES**. (`replica_operation_total{PUT}`
  is not instrumented for `file` replicas, so it is NOT used.)
- **Emit path (FR26):** only registry-state writes the log; the proven
  non-service precedent is `scripts/emit_signature_rejected.py` — build an
  `EventEnvelope`, append to the per-day JSONL under `flock(LOCK_EX|LOCK_NB)`,
  exit 3 on contention. Reused exactly.
- **New event:** `packages/events/src/events/types/replication.py` (mirrors
  `deployment.py`): payload + `register()` at 1.0.0 & 1.1.0, side-effect import.

## Acceptance Criteria

1. **AC1 — `replication.lagging` event.** `ReplicationLaggingPayload`
   (frozen/strict/forbid: `db`, `signal=Literal["sync_stalled"]`,
   `threshold_seconds>0`, `sustained_seconds≥0`, `sync_error_count≥0`), registered
   @1.0.0 + @1.1.0. **VERIFIED:** `check_event_registry` exit 0; unit test
   resolves it at 1.1.0.

2. **AC2 — pure debounce detector (unit-tested core).**
   `scripts/replication_lag_detector.py`: pure state machine —
   lagging = sync_count not advancing + sync_error rising; emit EXACTLY ONCE when
   sustained >5min; reset on recovery (so a new episode re-emits). No I/O.
   **VERIFIED:** 12 unit tests (no-lag / onset<5min / sustained→emit-once /
   no-re-emit / recovery→re-emit / stall-without-errors / boundary / json).

3. **AC3 — emit script.** `scripts/check_replication_lag.py`: stdlib-only
   (urllib) GET of `/metrics`, parse the two counters, load/save debounce state,
   run the detector, emit via the flock-guarded append on `should_emit`.
   **Story-13.4 FIX:** the day-file is created **0o660 + os.fchmod** (NOT the
   precedent's 0o640) — a 0o640 file from this external emitter would be
   group-non-writable and crash-loop registry-state's cross-uid recovery
   (Story 11.3.11; the exact event-log-file-mode bug). Others-triad stays 0.

4. **AC4 — `just litestream-lag-check` recipe + config.** Recipe runs the script;
   `litestream.yml.example` documents `addr: ":9090"` + the port-exposure choice.

5. **AC5 — metrics-subscriber family.** `replication` added to `_EVENT_FAMILIES`
   (bounded). ALL cardinality assertions bumped consistently:
   `test_metrics_state.py` ×2 (63→64) + `tests/integration/test_metrics_cardinality.py`
   (reconciled — it was STALE at 61: Stories 12.2/12.3 never propagated their
   +1s; real baseline was 63 → +1 replication = **64**, empirically verified by
   running the suite green) + the `build_collectors` docstring. **VERIFIED:**
   metrics_state 28 pass; integration cardinality 8 pass (non-slow) at 64.

6. **AC6 — synthetic 6-min S3-block E2E (live AC).** Deferred to operator/nightly
   (needs a real replica + network blocking). The hermetic detector unit tests +
   the registry/cardinality tests are the verification here; the live "exactly
   one event over 6min, stops on recovery" is the operator/nightly check.

7. **AC7 — gates + review.** ruff/format clean; mypy --strict 44=baseline (0 in
   touched files); check_event_registry + discipline green; unit + cardinality
   tests green. Authored by executor; **diff-audited by the orchestrator (separate
   pass)** which caught+fixed the CRITICAL file-mode bug; + code-review lane.

## Constraints
- **FR26:** emit ONLY via the flock-guarded direct append (exit 3 on contention).
- **NO new deps** (stdlib urllib).
- **Audit log never world-readable** — 0o660 (group-rw), others=0.
- **NO `mcp_clients.py`** touched. **VERIFIED.**

## Dev Agent Record

### Agent Model Used
claude-opus-4-8[1m] — 2 verification subagents (litestream docs + emit-path explore) → executor (opus) impl → orchestrator diff-audit. 2026-06-02.

### Diff-audit findings (orchestrator, separate pass)
- **CRITICAL FIXED:** emit script created the day JSONL at **0o640** (copied from
  the pre-11.3.11 `emit_signature_rejected.py`). An external emitter creating a
  group-non-writable file crash-loops registry-state's cross-uid recovery (the
  documented `event-log-file-mode-0640-cross-uid-gap`). Fixed → 0o660 + `os.fchmod`
  to defeat umask 022 (mirrors event_log.py). NOTE: `emit_signature_rejected.py`
  has the SAME latent 0o640 bug — flagged as a tiny pre-existing follow-up.
- **VERIFIED (not trusted):** the `test_metrics_cardinality.py` 61→64
  reconciliation is correct — confirmed 61 was stale on `main` (12.2/12.3 missed
  it) and ran the suite green at 64.
- No `os.environ.copy`; mcp_clients untouched; flock pattern reused; mypy
  44=baseline.

### File List
- packages/events/src/events/types/replication.py (NEW — payload + register 1.0.0/1.1.0)
- packages/events/src/events/types/__init__.py (M — side-effect import)
- packages/events/src/events/__init__.py (M — import + re-export)
- packages/events/src/events/types/test_replication.py (NEW — payload + registry tests)
- scripts/replication_lag_detector.py (NEW — pure debounce state machine)
- scripts/test_replication_lag_detector.py (NEW — detector unit tests)
- scripts/check_replication_lag.py (NEW — emit script; 0o660 file-mode FIX)
- justfile (M — litestream-lag-check recipe)
- litestream.yml.example (M — addr/metrics docs)
- docs/operator-runbook.md (M — lag-monitoring section)
- services/metrics-subscriber/.../app/metrics.py (M — replication family)
- services/metrics-subscriber/.../test_metrics_state.py (M — 63→64 ×2)
- tests/integration/test_metrics_cardinality.py (M — reconciled stale 61→64)

## Definition of Done
- `replication.lagging` registered @1.1.0; emitted via FR26 flock append at 0o660.
- pure detector unit-tested (emit-once + recovery-reset).
- `just litestream-lag-check` + metrics `addr` documented.
- `replication` family + ALL cardinality bounds bumped (verified green).
- diff-audit CRITICAL fix applied; code review discharged.
- `sprint-status.yaml` flips `13-4-litestream-lag-check-replication-lagging` to done → **Epic 13 complete**.

## Frontmatter
```yaml
---
story_id: 13.4
story_key: 13-4-litestream-lag-check-replication-lagging
parent_epic: 13
phase: 2
fr_refs: [NFR-R7]
nfr_refs: [NFR-R7]
arch_refs:
  - "ADR-0007 — litestream WAL replication; this closes the Epic-13 lag-observability gap"
  - "Story 11.3.11 — event-log file mode 0o660 (the cross-uid rule the emit script must honor)"
  - "scripts/emit_signature_rejected.py — the FR26 flock-append emit precedent reused"
  - "deployment.py event-type module — the types-module registration pattern mirrored"
estimated_complexity: MEDIUM (new event + detector + script + recipe + metrics family + tests)
priority: MEDIUM (NFR-R7; Epic-13 capstone)
blocks: []
unblocks:
  - operators get alerted on a silently-stalled replica (closes Epic 13)
---
```
