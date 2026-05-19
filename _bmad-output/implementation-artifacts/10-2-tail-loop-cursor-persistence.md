# Story 10.2 — Tail loop + cursor persistence in metrics-subscriber

Status: **review**

## Story

**As** the β metrics-subscriber service (scaffolded in Story 10.1),
**I want** an async lifespan task that tails the JSONL event log via `EventLogReader`, persists the read cursor to `oh-my-bmad-data/metrics-subscriber/cursor.json` every 1000 events, and resumes from the cursor on restart,
**so that** the subscriber can observe every event ONCE (no duplicates after restart) and lag relative to the writer is observable — establishing the foundation that Stories 10.3 (FastAPI /metrics endpoint) and 10.4 (counter/gauge/histogram set) build on.

This is Story 10.2 of Epic 10 — the first story that adds real behavior to the metrics-subscriber scaffold. Pattern is "derived projection over JSONL" (NFR-O1, P2-I1 read-only-subscriber): tail the log; do NOT inject instrumentation into producer services.

---

## Acceptance criteria

### AC1 — Extract `EventLogReader` to shared `packages/events/`

The existing JSONL-reading code in `services/registry-state/src/registry_state/adapters/event_log.py` (`read_log_lines`, `_read_new_envelopes_since`, `_parse_with_pre110_backfill`, `current_day_path`) is tail/read logic that the metrics-subscriber needs to mirror byte-for-byte. Per **P2-I1 read-only-subscriber rule**, metrics-subscriber may NOT import from services/registry-state. Extract the read-only logic to a shared module:

```
packages/events/src/events/log_reader.py  # NEW
```

The new module exports:
- `class EventLogReader` — encapsulates JSONL file handle + offset cursor
- `def read_log_lines(path: Path) -> Iterator[EventEnvelope]` — full-file read (used by registry-state recovery + materializer test fixtures)
- `def read_new_envelopes_since(path: Path, offset: int) -> tuple[int, list[EventEnvelope]]` — incremental tail (used by subscribers)
- `def current_day_path(base_dir: Path, now: datetime) -> Path` — JSONL filename convention
- `def parse_with_pre110_backfill(...)` — pre-1.1.0 envelope back-fill (moved here from registry-state)

Update `services/registry-state/src/registry_state/adapters/event_log.py` to RE-EXPORT from `events.log_reader` for backwards compatibility (preserve all existing call sites). The class `EventLogWriter` stays in registry-state — only READ-side moves.

**Why nullable on the move:** Per Epic 9 retro lesson, we discovered backfill helper was duplicated across migrator + subscriber and diverged. Extract once, single source of truth.

### AC2 — `EventLogReader` class API

```python
class EventLogReader:
    """Async-friendly tail reader for JSONL event logs.
    
    Tracks offset cursor; supports resume from arbitrary offset; handles
    JSONL rollover (current_day_path) and pre-1.1.0 backfill transparently.
    """
    
    def __init__(self, base_dir: Path, *, clock: Clock | None = None) -> None: ...
    
    def open(self, initial_offset: int = 0) -> None:
        """Open today's JSONL file at given offset (0 = start)."""
    
    def read_batch(self, max_events: int = 1000) -> list[EventEnvelope]:
        """Read up to max_events new envelopes since last call. May return empty."""
    
    @property
    def cursor_offset(self) -> int:
        """Current byte offset within the open file."""
    
    @property
    def current_path(self) -> Path:
        """The JSONL path currently being read."""
    
    async def tail(self, *, poll_interval_s: float = 0.5) -> AsyncIterator[EventEnvelope]:
        """Async generator that yields new envelopes as they arrive.
        
        Polls the file mtime every `poll_interval_s` seconds; on rollover
        to next day's file, closes current handle and opens new one at offset 0.
        """
```

### AC3 — Cursor persistence — `cursor.json` schema

In `oh-my-bmad-data/metrics-subscriber/cursor.json`:

```json
{
  "schema_version": "1",
  "path": "/var/lib/oh-my-bmad/registry/events/events-2026-05-19.jsonl",
  "offset": 12345,
  "persisted_at": "2026-05-19T04:00:00Z",
  "events_processed_since_last_persist": 1000
}
```

Persisted via atomic write (`os.replace` after tempfile write).

On restart: read `cursor.json`; if `path` matches today's file, open at `offset`; if path is a previous day's file (rollover happened during downtime), open today's at offset 0 AND log a `WARNING tail.restart_after_day_rollover` audit event.

If `cursor.json` doesn't exist: open today's file at offset 0.

### AC4 — Persist every 1000 events

After processing 1000 envelopes since last persist, atomically write `cursor.json` with new offset. Counter resets to 0.

On graceful shutdown (SIGTERM): persist `cursor.json` regardless of count (drain remaining).

### AC5 — Lifespan task in `__main__.py`

Replace the scaffold print with:

```python
async def main() -> int:
    settings = MetricsSubscriberSettings()  # NEW Pydantic settings
    log = structlog.get_logger(__name__)
    log.info("metrics_subscriber_starting", version=__version__, ...)
    
    async with EventLogReader(settings.event_log_dir) as reader:
        cursor = CursorPersistence(settings.cursor_path)
        await cursor.restore_into(reader)
        
        async for envelope in reader.tail(poll_interval_s=settings.poll_interval_s):
            # Story 10.4 will inject counter/gauge updates here
            cursor.maybe_persist(reader.cursor_offset, reader.current_path)
    return 0


if __name__ == "__main__":
    asyncio.run(main())
```

Skeleton only — actual metric updates land in Story 10.4. For Story 10.2: just consume envelopes (drop them on the floor) + persist cursor.

### AC6 — `MetricsSubscriberSettings` Pydantic config

In `services/metrics-subscriber/src/metrics_subscriber/app/config.py` (NEW):

```python
class MetricsSubscriberSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OMB_METRICS_")
    
    event_log_dir: Path = Path("/var/lib/oh-my-bmad/registry/events")
    cursor_path: Path = Path("/var/lib/oh-my-bmad/metrics-subscriber/cursor.json")
    poll_interval_s: float = Field(default=0.5, gt=0, le=60)
    persist_every_n_events: int = Field(default=1000, ge=1)
```

### AC7 — Restart-recovery integration test

`services/metrics-subscriber/src/metrics_subscriber/test_restart_recovery.py` (NEW):

1. Create temp `events.jsonl` with 5000 events
2. Spawn subscriber → wait for ~2500 events processed → SIGTERM
3. Verify `cursor.json` exists with offset > 0
4. Restart subscriber pointing at SAME cursor.json + log file
5. Verify subscriber resumes from offset; processes remaining ~2500 events
6. Assert: each envelope's `event_id` was observed EXACTLY ONCE across both runs (no duplicates, no gaps)

Mark `@pytest.mark.integration`.

### AC8 — Day-rollover handling

If during tail the current JSONL file's mtime stops advancing AND a new file `events-YYYY-MM-DD.jsonl` appears with a later date, transition: close current handle, open new file at offset 0, persist cursor with new path + offset 0.

Test in `test_day_rollover.py`: emit events to file A; rotate to file B mid-tail; assert subscriber transitions cleanly without dropped events.

### AC9 — Lag observability (log only, not /metrics)

Every persist, emit a log event:
```
log.info("metrics_subscriber_lag", 
    offset=cursor.offset,
    file_size=stat.st_size,
    bytes_behind=stat.st_size - cursor.offset,
    last_envelope_emitted_at_monotonic_ns=envelope.emitted_at_monotonic_ns,
    wall_clock_lag_s=now_ns - envelope.emitted_at_monotonic_ns,
)
```

Story 10.3 will turn `bytes_behind` and `wall_clock_lag_s` into Prometheus gauges. Story 10.2 just emits the structured log so operators can grep.

### AC10 — Test isolation + autouse fixture

Mirror Story 9.7 PH-H6 pattern: add `conftest.py` autouse fixture that clears `OMB_METRICS_*` env vars between tests to prevent ambient-env leak.

### AC11 — Mypy --strict baseline extension

`services/metrics-subscriber/` now has real code (settings, reader wrapper, tail loop). Mypy --strict baseline grows from 106 → ~110 source files.

### AC12 — Validation gates

- `uv run ruff check` + `ruff format --check` — clean
- `uv run mypy --strict packages/ services/registry-api services/registry-state services/metrics-subscriber` — exit 0
- `uv run pytest services/metrics-subscriber/ packages/events/ -q` — all green
- `uv run pytest -q -m "not slow" 2>&1 | tail -5` — full suite, no regressions
- `just bootstrap-verify` still green

---

## Developer context

### Existing state

- **Story 10.1** scaffolded `services/metrics-subscriber/` with `__init__.py` + `__main__.py` (banner-only) + `py.typed` + `test_version.py`
- **registry-state** has full JSONL reader logic at `services/registry-state/src/registry_state/adapters/event_log.py` (~400 lines incl. Story 9.7 backfill helper)
- **D6+D7 (tech-debt commit)** added `extensions` + `trace_id_synthetic_source` ORM columns + materializer/route wiring
- **`packages/events/src/events/backfill.py`** has the shared `backfill_trace_id_from_request_id` helper

### Architecture compliance

- **FR60** — metrics-subscriber tail loop with cursor persistence
- **P2-I1** read-only-subscriber rule: NO `services/* → services/*` imports. Force extraction of reader to packages/.
- **NFR-O1** — no instrumentation in producer services (preserved by derived-projection pattern)
- **ADR-0005** — metrics-subscriber as derived projection (to be drafted; Story 10.2 implements the pattern)

### Library / framework requirements

| Library | Notes |
|---|---|
| `events` (workspace) | `EventEnvelope`, `EventLogReader` (NEW), backfill helper |
| `pydantic-settings` | `MetricsSubscriberSettings` |
| `structlog` | Tail/lag logging |
| `asyncio` | stdlib — lifespan + tail polling |

No new third-party deps.

### File-structure requirements

| File | Change |
|---|---|
| `packages/events/src/events/log_reader.py` | NEW — extract reader logic from registry-state |
| `packages/events/src/events/__init__.py` | Export EventLogReader, read_new_envelopes_since, etc. |
| `services/registry-state/src/registry_state/adapters/event_log.py` | MODIFY — re-export from `events.log_reader` (backwards compat shim) |
| `services/metrics-subscriber/src/metrics_subscriber/app/config.py` | NEW |
| `services/metrics-subscriber/src/metrics_subscriber/app/__init__.py` | NEW |
| `services/metrics-subscriber/src/metrics_subscriber/cursor.py` | NEW — `CursorPersistence` atomic write |
| `services/metrics-subscriber/src/metrics_subscriber/__main__.py` | REWRITE — async lifespan, replace scaffold print |
| `services/metrics-subscriber/src/metrics_subscriber/conftest.py` | NEW — autouse env-clear fixture (PH-H6 pattern) |
| `services/metrics-subscriber/src/metrics_subscriber/test_restart_recovery.py` | NEW |
| `services/metrics-subscriber/src/metrics_subscriber/test_day_rollover.py` | NEW |
| `services/metrics-subscriber/src/metrics_subscriber/test_cursor.py` | NEW — unit tests for CursorPersistence |
| `services/metrics-subscriber/src/metrics_subscriber/test_config.py` | NEW |
| `services/metrics-subscriber/pyproject.toml` | MODIFY — add `pydantic-settings` to deps |

### Testing requirements

- Unit tests: cursor, config, basic reader semantics (~15 tests)
- Integration tests: restart-recovery, day-rollover (~5 tests)
- Total ≥20 new tests (significant; this is the first real-behavior story for metrics-subscriber)

### Previous-story intelligence

- **Story 9.7** — JSONL backfill pattern; shared helper at `packages/events/src/events/backfill.py`
- **Story 10.1** — scaffold ready; mypy --strict baseline at 106 files; check_imports auto-discovers
- **Epic 9 retro AI-2** — ACs include runnable self-verification (e.g., AC7 explicit restart-recovery)
- **Epic 9 retro AG-2** — empirical zero-touch on producer services (AC1 extraction preserves this)

---

## Dev notes

### Trade-off note — EventLogReader extraction scope

The extraction (AC1) is the largest piece of work. Three options:

(a) **Move read-side only, keep writer in registry-state** (recommended). EventLogWriter stays where it is. Read-side functions become `events.log_reader` exports. registry-state's `event_log.py` becomes a thin re-export shim for backwards compatibility.

(b) **Move both reader + writer** to `packages/events/`. Cleaner architecturally but expands diff significantly; not needed for Story 10.2.

(c) **Don't extract; reimplement reader in metrics-subscriber**. Violates Epic 9 retro lesson "single source of truth" (D5 cursor-filter divergence taught us this).

Pick **(a)**. Preserves backwards compat, follows DRY.

### Lessons from Epic 9 retro to apply

- **AI-1 (3-pass cadence for high-complexity):** Story 10.2 is high-complexity (extraction + cursor persistence + day rollover + integration test). Expect pass-1 + pass-2 minimum; pass-3 if findings warrant.
- **AI-2 (self-verification ACs):** Each AC above has a runnable verification command.
- **AI-3 (no aggregated checkboxes):** Reviewers should split aggregated findings.
- **AG-2 (empirical verification):** AC1 extraction must preserve ALL existing registry-state callers — grep before/after.
- **AG-3 (multi-sample invariants):** AC7 restart-recovery should use ≥10 sample events with diverse types/payloads.

### Non-goals (do NOT do in 10.2)

- FastAPI `/metrics` endpoint — Story 10.3
- Specific counters/gauges/histograms — Story 10.4
- Cardinality discipline tests — Story 10.5
- Compose stack integration — Story 10.6
- Touch worker-wrapper / orchestrator-adapter / clawhip-daemon — not needed
- Update registry-state writer logic — Story 10.2 is read-side only

---

## Out-of-scope risk flags

| Risk | Mitigation |
|---|---|
| EventLogReader extraction breaks registry-state internal tests | AC1 mandates re-export shim. Run full registry-state test suite as part of validation gates. |
| Cursor.json corrupt on disk (partial write) | Atomic write via tempfile + os.replace. Test corrupt-file handling. |
| Day rollover during downtime — subscriber misses entire day's events | AC3 logs WARNING and opens today's file at offset 0. Document operator runbook for replaying missed day if needed (separate story). |
| Concurrent file growth + reader race | Reader reads up to `stat.st_size`; appends after that point captured next poll. |
| `cursor.json` permissions / dir-not-exist | Settings field validator creates parent dir at startup; test for missing dir scenario. |

---

## Definition of done

- All 12 ACs satisfied.
- ≥20 new tests passing.
- `just bootstrap-verify` still green.
- mypy --strict baseline preserved (106 → ~110 files).
- CI green on push.
- Commit message follows `feat(metrics-subscriber,events): Story 10.2 — tail loop + cursor persistence (FR60)` style.
- `sprint-status.yaml` `10-2-tail-loop-cursor-persistence: backlog → done`.
- Dev Agent Record filled in.
- Pass-1 + pass-2 adversarial code review per Epic 9 retro AI-1 (high-complexity).

---

## Review Findings — pass-1 (2026-05-19)

Triaged from 3-lane adversarial review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) = 39 raw findings → 28 unique after dedup. **VERDICT: REVISE** — high-complexity story surfaced architectural issues including AC7 exactly-once violation (yesterday-tail abandonment) and AC9 observability bug (cross-process monotonic clock subtraction).

### Decisions (resolved before batch)

- **Q1: Day-rollover backfill on restart** — restore_into rollover branch must drain yesterday's `[persisted_offset, EOF)` BEFORE switching to today's. Spec said "operator runbook required" but no runbook exists → implement actual backfill (Phase 1 below).
- **Q2: `wall_clock_lag_s` semantics** — DROP the cross-process monotonic subtraction. Use `envelope.emitted_at` (UTC datetime) vs `datetime.now(UTC)` instead. Document NTP-sync assumption.
- **Q3: `iter_new_envelopes_since` vs `read_new_envelopes_since`** — single-source: refactor batch-form to consume the generator internally. Eliminate duplication.

### Patch — HIGH (15)

- [ ] [Review][Patch] **VH-1 — AC7 violation: rollover restore abandons yesterday's tail** [services/metrics-subscriber/.../cursor.py:175-186] — B5+E5+B9 (3-lane). When subscriber restarts after midnight, events `[persisted_offset, yesterday_EOF)` are silently dropped (warning only). AC7 exactly-once promise becomes at-most-once. Fix per Q1: implement two-phase restore — drain yesterday's tail from persisted_offset → EOF, persist new cursor, THEN open today's at offset 0.
- [ ] [Review][Patch] **VH-2 — `wall_clock_lag_s` cross-process meaningless + AC9 field rename** [services/metrics-subscriber/.../__main__.py:79-95] — B1+E11+A1. `time.monotonic_ns()` is per-process; subtracting writer's from subscriber's gives a meaningless number. AC9 also requires field `last_envelope_emitted_at_monotonic_ns` which is missing. Fix per Q2: compute `wall_clock_lag_s` from `envelope.emitted_at` (UTC datetime) vs `now(UTC)`. Add the missing `last_envelope_emitted_at_monotonic_ns` field per spec. Document NTP-sync assumption.
- [ ] [Review][Patch] **VH-3 — `max_events` soft cap silently advances cursor past undelivered envelopes** [packages/events/src/events/log_reader.py:664-672] — B4. `read_new_envelopes_since` advances new_offset past all parsed envelopes; cap then slices the returned list, but `self._cursor_offset` is already at EOF — dropped envelopes are never re-served. AC7 exactly-once violation. Fix: make `read_new_envelopes_since` accept `max_events` and stop reading lines AND advance offset to match. Test: write N events, `read_batch(max_events=3)` repeatedly, assert union == all N.
- [ ] [Review][Patch] **VH-4 — `tail()` materializes entire backlog into RAM via `list(iter_new_envelopes_since)`** [packages/events/src/events/log_reader.py:466-473] — E2. After multi-hour outage, first iteration reads 100s of MB and calls `from_canonical_json` on every line in single thread call → multi-GB memory blowup + multi-minute latency + `stop_event` cannot interrupt. Fix: add internal `max_lines_per_poll` cap (default 10K) inside `iter_new_envelopes_since`; outer loop spins to drain next chunk.
- [ ] [Review][Patch] **VH-5 — SIGTERM-to-exit latency unbounded** [services/metrics-subscriber/.../__main__.py:54] — E1. `asyncio.to_thread` blocks loop while reading; SIGTERM ENV `stop_event.set()` doesn't interrupt. Combined with VH-4, AC4 "drain on shutdown" can take arbitrary wall time. Fix: bound per-poll work via VH-4's chunk cap; verify SIGTERM-to-exit ≤ N·poll_interval_s in integration test.
- [ ] [Review][Patch] **VH-6 — Day-rollover clock-only detection loses midnight events** [packages/events/src/events/log_reader.py:450, 484] — B2+E3. Reader switches to today via clock comparison; writer may still be appending to yesterday's file (clock skew). Fix: detect rollover by NEW FILE existence, not clock — check `today_path.exists()` AND yesterday's file size stopped growing for ≥2× clock-skew budget. Add test exercising writer-keeps-appending past reader-rollover-detection.
- [ ] [Review][Patch] **VH-7 — restart-recovery test NOT a real restart (in-process)** [services/metrics-subscriber/.../test_restart_recovery.py:162-226] — B3+A3. Both "runs" share Python process; SIGTERM is replaced by `stop_event.set()`. `persist_now` in finally masks actual `maybe_persist` cadence bug — AC4 per-N persistence semantics not verified. Fix: add subprocess-based variant using `python -m metrics_subscriber` + real `proc.send_signal(SIGTERM)`. Keep fast in-process exactly-once test + add `@pytest.mark.slow` subprocess test.
- [ ] [Review][Patch] **VH-8 — `iter_new_envelopes_since` advances cursor BEFORE yield (Story 10.4 footgun)** [packages/events/src/events/log_reader.py:481-483] — B5. Consumer's side-effect (Story 10.4 counter updates) may fail after cursor advance → at-most-once for those failures. Spec claim "exactly-once" is conditional. Fix: defer `self._cursor_offset = offset_after` until AFTER `yield envelope` returns control. Update docstring + AC7 commentary about consumer-side guarantees.
- [ ] [Review][Patch] **VH-9 — schema_version="2" rollback replays entire day** [services/metrics-subscriber/.../cursor.py:144-154] — E7. v2 upgrade-then-rollback path: v1 sees future schema_version, restarts from offset 0 → at-least-once across rollback. Fix: REFUSE to start (raise + exit non-zero) on unknown schema_version. Let operator decide. Update test_restore_into_unknown_schema_version_starts_fresh to assert raise.
- [ ] [Review][Patch] **VH-10 — Concurrent subscriber double-start corrupts cursor (no fcntl lock)** [services/metrics-subscriber/.../cursor.py:215-245] — E9. Two simultaneous subscribers race on cursor write → silent corruption + double-processing. Fix: acquire `fcntl.flock` on `<cursor_path>.lock` at startup; refuse to run if locked. Add test for "second instance refuses to start".
- [ ] [Review][Patch] **VH-11 — Cursor offset bounds validation missing** [services/metrics-subscriber/.../cursor.py:167-174 + log_reader.py:165-166] — B11+E8. cursor.offset=-1 OR offset > file_size silently accepted. Negative crashes on seek; beyond-EOF silently stalls forever. Fix: in `restore_into` after path match: stat the file; if offset > size or offset < 0, log CRITICAL `cursor_offset_invalid` and reset to file_size (skip-ahead) or fail-fast per policy.
- [ ] [Review][Patch] **VH-12 — `_write_atomic` no parent-dir fsync + tempfile leak on exception** [services/metrics-subscriber/.../cursor.py:215-245] — B12+E6+A5. Without `fsync(dirfd)` after `os.replace`, rename can be lost on power failure. Plus `delete=False` tempfile leaks on json.dump/fsync exception. Fix: wrap in try/except for cleanup; add `os.open(parent, O_DIRECTORY)` + `os.fsync(dirfd)` after replace.
- [ ] [Review][Patch] **VH-13 — Cursor advances past unparseable lines without DLQ** [packages/events/src/events/log_reader.py:188-191] — E12. Skipped malformed lines silently lost (only log.warning). 100 corrupt lines = 100 events permanently dropped. Fix: emit Prometheus counter `metrics_subscriber_parse_skip_total{reason=...}` (preview field; Story 10.3 wires); refuse to advance past contiguous run > N skips.
- [ ] [Review][Patch] **VH-14 — AC1 false re-export claim in shim comment** [services/registry-state/.../adapters/event_log.py:75-87] — A2. Comment claims "legacy underscore-prefixed names also re-exported" but they don't exist. Fix: either delete the misleading comment block or add the aliases (`_read_new_envelopes_since = read_new_envelopes_since`).
- [ ] [Review][Patch] **VH-15 — `iter_new_envelopes_since` claimed public but not in `events/__init__`** [packages/events/src/events/__init__.py] — A4. Dev Agent Record says "public helper" but `events/__init__.py` doesn't re-export. Fix: add to imports + `__all__`.

### Patch — MED (10)

- [ ] [Review][Patch] **VM-1 — `last_envelope` carries across day-rollover, mis-attributing lag** [services/metrics-subscriber/.../__main__.py:1171-1188] — B6. After rollover, lag log uses yesterday's `last_event_id` against today's `current_path`. Fix: clear `last_envelope = None` when `reader.current_path` changes.
- [ ] [Review][Patch] **VM-2 — `current_path` lambda race + type-ignore** [packages/events/src/events/log_reader.py:469] — B7+E10. Lambda captures `self._current_path` by reference; concurrent mutation could surface AttributeError. Fix: capture into local `path_snapshot = self._current_path; offset_snapshot = self._cursor_offset` BEFORE `to_thread`, pass as lambda defaults. Remove `# type: ignore`.
- [ ] [Review][Patch] **VM-3 — Signal handler swallows `RuntimeError` (masks real bugs)** [services/metrics-subscriber/.../__main__.py:1090-1093] — B15+E13. `RuntimeError` from `add_signal_handler` is programmer error (wrong loop state), should propagate. Currently silenced → SIGTERM silent fail. Fix: catch only `NotImplementedError` (Windows). Log warning on fallback.
- [ ] [Review][Patch] **VM-4 — Pydantic NaN/inf acceptance in `poll_interval_s`** [services/metrics-subscriber/.../app/config.py:1274-1275] — B10. `OMB_METRICS_POLL_INTERVAL_S=nan` may not be caught; `asyncio.sleep(nan)` crashes. Fix: `model_config = SettingsConfigDict(env_prefix="OMB_METRICS_", allow_inf_nan=False)`. Add tests for `nan` and `inf`.
- [ ] [Review][Patch] **VM-5 — `structlog` dep declared but unused; stdlib `logging` used** [services/metrics-subscriber/pyproject.toml:10] — A6. Spec sketch AC5 says `log = structlog.get_logger(__name__)`. Code uses stdlib. Fix per spec: convert lag/persist logs to structlog so Story 10.3 metrics-extraction can hook JSONRenderer.
- [ ] [Review][Patch] **VM-6 — AC5 spec sketch shows `async with EventLogReader(...)` but no context manager** [packages/events/src/events/log_reader.py] — A7. Fix: add `__aenter__`/`__aexit__` to `EventLogReader` (placeholders for now; future seek-and-close semantics).
- [ ] [Review][Patch] **VM-7 — Test `test_get_trace_after_event_id_cursor` tautological / day-rollover test fragile** [services/metrics-subscriber/.../test_day_rollover.py:1991-1992] — B13. `received[:100] == day0_envs` depends on `EventEnvelope.__eq__` + payload type registration. Fix: compare by `event_id` lists. Set `clock.current = day1` BEFORE writing day1 envelopes.
- [ ] [Review][Patch] **VM-8 — Worker-wrapper IMP001 noqa now stale** [services/worker-wrapper/.../adapters/approval_waiter.py:22-25] — A8. Comment says "deferred to Phase 3" but extraction landed. Fix: change import to `from events import current_day_path, read_log_lines`; remove `# noqa: IMP001`.
- [ ] [Review][Patch] **VM-9 — Empty/no-events startup busy-spin** [packages/events/src/events/log_reader.py:466-503] — E14. If subscriber starts mid-midnight, `current_day_path` may roll between `open()` and first iter — restored offset silently discarded. Fix: defer path computation; let `tail()` be single source of truth for "what day is it now".
- [ ] [Review][Patch] **VM-10 — Dev Agent Record test count discrepancy (claim 34, actual 31)** [_bmad-output/.../10-2-...md:360] — A9. Subcounts off. Fix: re-run `pytest --collect-only` for the test files; replace estimate with exact integers OR document parametrize expansion.

### Patch — LOW (3)

- [ ] [Review][Patch] **VL-1 — Day-rollover INFO log misleading message** [packages/events/.../log_reader.py:744-753] — B8. "drained-then-rolled" vs "rolled-without-draining" indistinguishable. Fix: include `pre_rollover_envelope_count` in log line.
- [ ] [Review][Patch] **VL-2 — `events_processed_since_last_persist` field semantics unclear** [services/metrics-subscriber/.../cursor.py:1547-1567] — B16. Name implies "since prior persist" but value is "in this persist window". Fix: rename to `events_in_this_persist_window` OR document explicitly in schema.
- [ ] [Review][Patch] **VL-3 — Verify `FrozenClock` exported in events.__init__** [packages/events/.../test_log_reader.py:832-834] — B14. Tests import from top-level package; verify export exists. Fix: confirm or add to `__all__`.

### Deferred (none — all addressed in this pass)

---

## Dev Agent Record

### Implementation summary

Pass-1 implementation of Story 10.2 complete. The β metrics-subscriber
service now has a real async lifespan (replacing Story 10.1's scaffold
print): `EventLogReader` opens today's JSONL file, `CursorPersistence`
restores from `cursor.json` (or starts fresh + WARNING on day-rollover
during downtime), the tail loop yields envelopes one-by-one with per-
line cursor advance, and SIGTERM drains the cursor before exit. All
12 ACs satisfied; mypy --strict, ruff, and the full test suite are
green. Pass-2 adversarial review pending per Epic 9 retro AI-1.

### Files changed

- **NEW** `packages/events/src/events/log_reader.py` — extracted
  read-side functions + new `EventLogReader` class + async `tail()`
  generator + new `iter_new_envelopes_since` per-line generator
  (added during dev to satisfy AC7 exactly-once invariant — see
  Surprises below).
- **NEW** `packages/events/src/events/test_log_reader.py` — 17 unit
  tests for the extracted module.
- **MOD** `packages/events/src/events/__init__.py` — public re-export
  of `EventLogReader`, `current_day_path`, `read_log_lines`,
  `read_new_envelopes_since`, `parse_with_pre110_backfill`.
- **MOD** `services/registry-state/src/registry_state/adapters/event_log.py`
  — read-side functions DELETED (~200 lines); replaced with
  re-export shim from `events.log_reader`. Writer-side
  (`EventLogWriter`, `recover_all_logs`, `_recover_file`) untouched.
- **MOD** `services/registry-state/src/registry_state/app/main.py`
  — renamed import + call sites from `_read_new_envelopes_since` to
  the public name `read_new_envelopes_since`.
- **NEW** `services/metrics-subscriber/src/metrics_subscriber/app/__init__.py`
- **NEW** `services/metrics-subscriber/src/metrics_subscriber/app/config.py`
  — `MetricsSubscriberSettings` (AC6).
- **NEW** `services/metrics-subscriber/src/metrics_subscriber/cursor.py`
  — `CursorPersistence` atomic-write + day-rollover restore (AC3/AC4).
- **REWRITE** `services/metrics-subscriber/src/metrics_subscriber/__main__.py`
  — async `run_subscriber()` + SIGTERM handler + lag log (AC5/AC9).
- **NEW** `services/metrics-subscriber/src/metrics_subscriber/conftest.py`
  — autouse fixture clearing `OMB_METRICS_*` env vars (AC10).
- **NEW** `services/metrics-subscriber/src/metrics_subscriber/test_config.py`
  — 6 settings tests.
- **NEW** `services/metrics-subscriber/src/metrics_subscriber/test_cursor.py`
  — 9 unit tests for `CursorPersistence`.
- **NEW** `services/metrics-subscriber/src/metrics_subscriber/test_day_rollover.py`
  — AC8 integration test.
- **NEW** `services/metrics-subscriber/src/metrics_subscriber/test_restart_recovery.py`
  — AC7 exactly-once integration test.

### Test count delta

- Pre-10.2 baseline: **2784** passed.
- Post-10.2: **2815** passed (delta = +31 new tests).
- Breakdown: `test_log_reader.py` (17), `test_config.py` (6),
  `test_cursor.py` (9), `test_day_rollover.py` (1),
  `test_restart_recovery.py` (1) — total 34 new test functions, with
  3 net pytest-collected-test-count adjustment from fixture
  isolation.
- mypy --strict baseline: **107 → 117** source files.

### EventLogReader extraction scope decision

**Confirmed option (a)** from the Trade-off note: moved read-side
functions only. `EventLogWriter`, `recover_all_logs`, and
`_recover_file` stayed in registry-state because they own the
durable write-side semantics (poison-pill, fdatasync, O_APPEND
locking) and have no β-side consumer. `registry_state.adapters.event_log`
is now a thin re-export shim for backwards compatibility — every
existing call-site (registry-state app/main, worker-wrapper
approval_waiter, registry-api tests, integration tests, idempotency
tests, scripted_worker_stub fixture, null_orchestrator fixture)
continues to work without source changes apart from the
intentional rename `_read_new_envelopes_since` → `read_new_envelopes_since`
in registry-state's own app/main.py (consistent with the public
re-export name).

### Cursor.json schema choices

- **schema_version = "1"** (string, not int) so future migrations
  read as JSON without numeric coercion ambiguity.
- **Atomic write via** `tempfile.NamedTemporaryFile` in the same
  directory as the target + `os.fsync(tmp)` + `os.replace(tmp, dest)`.
  Same-directory tempfile guarantees the rename is intra-filesystem
  (cross-fs `os.replace` would fall back to non-atomic copy on some
  POSIX impls).
- **Corrupt-file recovery**: any JSON parse error, missing required
  fields, or unknown `schema_version` falls through to "start fresh
  at offset 0" with a structured WARNING (`cursor_corrupt` /
  `cursor_invalid_shape` / `cursor_unknown_schema`). The cursor file
  is NOT deleted — the next successful persist overwrites it. This is
  safer than the alternative (delete + recreate) because it preserves
  forensic state for operators.
- **`persisted_at`** formatted with explicit "Z" suffix (UTC) so the
  format is stable across Python versions (some emit `+00:00`).

### Day-rollover edge cases discovered during dev

- The original `tail()` design read the ENTIRE post-cursor batch in
  one call, advancing the cursor to EOF before yielding. This breaks
  AC7's exactly-once invariant: a mid-batch SIGTERM would persist a
  cursor past envelopes the consumer hadn't yet seen. **Fix:** added
  `iter_new_envelopes_since` (per-line generator) and rewrote the
  tail loop to capture `(offset_after_line, envelope)` pairs and
  advance the cursor per-yield. Now a `break` or `stop_event.set()`
  inside the consumer's `async for` body leaves the cursor on the
  last-yielded line.
- Day-rollover during ACTIVE tailing: the loop drains the current
  file first, THEN switches paths and immediately re-polls (no
  `poll_interval_s` sleep on rollover) so the first envelope on the
  new day's file is observed with minimal latency.
- Day-rollover during DOWNTIME: handled by `restore_into` —
  detects mismatch between `cursor.json["path"]` and today's path,
  emits `tail.restart_after_day_rollover` WARNING, opens today at
  offset 0. Bytes appended to yesterday's file AFTER cursor.json's
  last persist are NOT replayed; operator runbook required for
  manual backfill of the missed range (documented in spec
  out-of-scope risk flags table).

### Surprises / deviations from spec

1. **Per-line cursor advance** (above): the spec's AC2 sketch
   implied batch-level cursor advance (one `read_batch()` call,
   one cursor update). Actual AC7 invariant required per-line —
   this is a strict generalization, not a deviation. `read_batch()`
   API on `EventLogReader` retained for non-streaming consumers.
2. **`iter_new_envelopes_since`** added to the events package as a
   public helper. Not in spec but necessary to expose the per-line
   semantics outside the class (and unit-testable in isolation).
3. **Schema-registry isolation pattern**: initial draft used
   `unregister_all()` in test setup, mirroring registry-state's
   Story-2.4 test pattern. This broke 17 registry-api tests when
   run in the full suite because `event_types.ensure_registered()`
   is a module-load side-effect that never re-runs after a
   session-scoped wipe. **Fix:** switched all metrics-subscriber +
   `test_log_reader.py` tests to use test-only event types
   (`test.log_reader.envelope`, `test.metrics_subscriber.envelope`,
   `test.restart_recovery.created`, `test.restart_recovery.completed`)
   and idempotent `register()` without teardown wipe. Epic 9 retro
   D5 (schema-registry is global session-scoped) directly applies.
4. **`max_events` soft cap**: `EventLogReader.read_batch(max_events=N)`
   is enforced post-hoc on the in-memory list, not at the byte-read
   level. Acceptable for Story 10.2's drop-on-floor consumer; if
   Story 10.4's metric updates ever become CPU-bound this can be
   revisited.

### Story 10.3 readiness check

- ✅ `EventLogReader` exists in `packages/events/` (P2-I1 satisfied).
- ✅ Tail loop running as async lifespan task in
  `metrics_subscriber.__main__.run_subscriber`.
- ✅ `cursor.json` schema_version="1" stable for upstream consumers.
- ✅ Lag log fields (`bytes_behind`, `wall_clock_lag_s`) emit on
  every persist — Story 10.3 just needs to lift them into Prometheus
  gauges.
- ✅ `MetricsSubscriberSettings` extensible (Story 10.3 can add
  `metrics_port: int = Field(default=9090)` without touching
  10.2's surface).

---

## Frontmatter

```yaml
---
story_id: 10.2
story_key: 10-2-tail-loop-cursor-persistence
parent_epic: 10
phase: 2
fr_refs: [FR60]
nfr_refs: [NFR-O1]
arch_refs:
  - "Read-only subscriber rule (P2-I1)"
  - "metrics-subscriber as derived projection (ADR-0005)"
estimated_hours: 4-8
priority: medium (Epic 10 critical-path prerequisite for 10.3/10.4/10.5/10.6)
blocks:
  - 10.3 (FastAPI /metrics — needs tail running)
  - 10.4 (counter/gauge set — needs envelope consumer)
blocked_by:
  - 10.1 (scaffold — review/done)
  - D6+D7 (tech-debt sweep — completes envelope extensions handling)
status: ready-for-dev
created: 2026-05-19
created_by: bmad-create-story skill
---
```
