# Story 13.4a — litestream silent-hang signal (`silent_stall`) (NFR-R7 follow-up)

Status: done

<!-- Follow-up filed from the Story 13.4 code-review: the 13.4 lag signal
(sync_count stall AND sync_error rising) missed a TOTALLY-hung litestream (loop
frozen → no sync attempts → errors flat). Validation-first: the key assumption
was EMPIRICALLY verified before any code. -->

## Story

**As** the platform operator,
**I want** the lag-check to also detect a litestream sidecar whose sync loop has
**silently frozen** (no errors, just stopped), not only one failing with S3
errors,
**so that** the most dangerous failure mode — a dead replica that looks fine —
surfaces as a `replication.lagging` event.

## Why this was a real gap (from 13.4 review)

Story 13.4's signal was `sync_count stalled AND sync_error_count rising`. A
hung/deadlocked/frozen litestream makes NO sync attempts, so `sync_error_count`
stays flat — the AND never fires. The code-reviewer flagged this HIGH/MEDIUM as a
documented design trade-off pending verification of one assumption.

## Validation-first: the assumption, empirically PROVEN

**Assumption:** does `litestream_sync_count` advance when the DB is idle (no
writes)? If yes, a *flat* sync_count unambiguously means the sync loop died (safe
to alarm). If no, a flat count during idle would false-positive.

**Proof (2026-06-02):** ran `litestream/litestream:0.3.13 replicate` against an
IDLE WAL db (zero writes) with `addr: ":9090"`; sampled `/metrics` 3× over 12s:
`sync_count` advanced **4 → 8 → 12** (~1/s) with `sync_error_count=0`. So
sync_count is a reliable ~1/s heartbeat independent of write activity →
**a flat sync_count is a sound hang signal with NO idle false-positives.** This
justifies a single sustained-threshold (5 min of flat heartbeat is unambiguous;
no need for a separate 2× window).

## Acceptance Criteria

1. **AC1 — episode starts on ANY stall.** The detector
   (`scripts/replication_lag_detector.py`) now opens a lag episode whenever
   `sync_count` stops advancing (regardless of errors), recording the
   error-count at onset (`onset_sync_error_count`). **VERIFIED** by unit tests.

2. **AC2 — signal derived at emit.** When sustained > threshold, emit ONCE with
   `signal = "sync_stalled"` if `sync_error_count` rose since onset (S3/network
   failures) else `"silent_stall"` (the hung loop). **VERIFIED:** dedicated unit
   tests for both classifications.

3. **AC3 — payload Literal extended additively.**
   `ReplicationLaggingPayload.signal: Literal["sync_stalled", "silent_stall"]` —
   a new enum *value* on the same 1.1.0 schema (not a field/shape change);
   `check_event_registry` green; mypy --strict packages/ clean.

4. **AC4 — emit-once + recovery-reset preserved** for both signal types; back-compat
   for pre-13.4a state files (`onset_sync_error_count` defaults to
   `last_sync_error_count`). **VERIFIED** by unit tests.

5. **AC5 — no metrics/cardinality change.** `signal` is a payload field, not a
   metrics label; the `replication` family already exists (13.4). No bound bump.

6. **AC6 — gates + review.** ruff/format clean; mypy clean; check_event_registry
   + discipline green; 40 detector+payload unit tests pass (was 37; +3 net). Live
   hung-sidecar E2E deferred to operator/nightly (as 13.4 AC6).

## Constraints
- **NO behavior regression** for the 13.4 `sync_stalled` path — all prior tests
  still pass (the recovery/emit-once/threshold semantics are unchanged).
- **No false-positives during idle** — justified by the empirical heartbeat proof.
- Pure detector stays I/O-free; emit path unchanged (FR26 flock, 0o660).

## Dev Agent Record

### Agent Model Used
claude-opus-4-8[1m] — validation-first (empirical litestream idle-heartbeat test) + direct implementation, 2026-06-02.

### Completion Notes List
- EMPIRICALLY verified sync_count advances ~1/s when idle (the gating assumption).
- Detector refactored to episode-on-any-stall + `onset_sync_error_count` state
  field; signal classified at emit (sync_stalled vs silent_stall).
- Payload `signal` Literal += "silent_stall" (additive enum value on 1.1.0).
- 4 new/updated unit tests (silent-stall sustained→emit; errors-rising→sync_stalled;
  stall-starts-episode; onset-error back-compat default). 40 total pass.
- Updated the detector blind-spot comment (now RESOLVED) + the operator-runbook
  signal description (pending — see File List).

### File List
- scripts/replication_lag_detector.py (M — episode-on-stall, onset_sync_error_count, signal derivation)
- scripts/test_replication_lag_detector.py (M — silent_stall + back-compat tests)
- packages/events/src/events/types/replication.py (M — signal Literal += silent_stall)
- docs/operator-runbook.md (M — silent_stall mention)

### Code Review — 2026-06-02, code-reviewer (separate lane)

- **code-reviewer:** APPROVE-WITH-NITS (0 CRITICAL/HIGH). Traced ALL state
  transitions = correct; no regression to the 13.4 sync_stalled path; onset
  baseline captured correctly (previous sample's error count); back-compat
  default safe; `>` boundary preserved; Literal extension additive (no consumer
  pattern-matches signal). Fixes applied: MEDIUM (stale module docstring → now
  describes episode-on-stall + silent_stall), LOW (payload test now parametrized
  over both signals), NIT (EmitFields.signal tightened to the Literal). 41 tests
  pass; ruff/mypy clean.

## Definition of Done
- silent-hang detected as `replication.lagging` signal="silent_stall".
- sync_stalled path unchanged; emit-once/recovery preserved; back-compat state.
- gates green; 40 unit tests pass; code review discharged.
- `sprint-status.yaml` flips `13-4a-litestream-silent-hang-signal` to done.

## Frontmatter
```yaml
---
story_id: 13.4a
story_key: 13-4a-litestream-silent-hang-signal
parent_epic: 13
phase: 2
fr_refs: [NFR-R7]
nfr_refs: [NFR-R7]
arch_refs:
  - "Story 13.4 — the lag-check this hardens; the AND-signal blind spot this closes"
  - "empirical proof 2026-06-02 — litestream sync_count advances ~1/s when idle (gating assumption)"
estimated_complexity: SMALL (pure-detector OR-branch + payload enum value + tests)
priority: MEDIUM (NFR-R7; closes the silent-hang gap)
blocks: []
unblocks:
  - operators are alerted on a silently-frozen replica, not just an erroring one
---
```
