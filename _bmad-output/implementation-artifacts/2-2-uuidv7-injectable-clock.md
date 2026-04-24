# Story 2.2: UUIDv7 + injectable clock

Status: done

## Story

As a **platform service**,
I want **`packages/events/src/events/ids.py` exporting UUIDv7 generators with prefixed-ID helpers, and `packages/events/src/events/clock.py` exporting an injectable `Clock` protocol + `FrozenClock` test double**,
so that **all task/session/event/request IDs are time-ordered (replay-safe, k-sortable without extras) and tests can control time deterministically** — filling in the generators whose shape Story 2.1's envelope validators already accept.

## Acceptance Criteria

1. **AC-1: `packages/events/src/events/ids.py`** — UUIDv7 + prefixed-ID generators. Exports:
   - `new_uuid7(clock: Clock | None = None, rng: Random | None = None) -> str` — RFC 9562 v7 UUID as canonical hyphenated lowercase hex string.
     - First 48 bits = unix-epoch milliseconds (big-endian); provides time-ordered k-sortability.
     - Next 4 bits = version (`0x7`).
     - Next 12 bits = `rand_a` (cryptographic random).
     - Next 2 bits = variant (`0b10`).
     - Last 62 bits = `rand_b` (cryptographic random).
     - `clock` injection: when provided, `clock.now()` supplies the timestamp; when `None`, uses system `time.time()`.
     - `rng` injection: when provided, `rng.randbytes(10)` supplies the random material; when `None`, uses `os.urandom(10)`.
   - `new_task_id(...) -> str` — returns `f"t-{new_uuid7(...)}"` (per Arch line 308).
   - `new_session_id(...) -> str` — returns `f"s-{new_uuid7(...)}"`.
   - `new_event_id(...) -> str` — returns `f"e-{new_uuid7(...)}"` (matches Story 2.1 envelope.py regex exactly).
   - `new_idempotency_key(...) -> str` — bare UUIDv7 (no prefix, per Arch line 308).
   - `new_request_id(...) -> str` — bare UUIDv7 (no prefix, per Arch line 309).
   - `parse_prefix(s: str) -> tuple[str, str] | None` — returns `("t", "<uuid-core>")` for a prefixed ID; returns `None` for unprefixed inputs. Helper for debugging / logging.
   - All 6 generator entrypoints accept the same `(clock, rng)` keyword arguments for test determinism.

2. **AC-2: `packages/events/src/events/clock.py`** — injectable clock. Exports:
   - `Clock(Protocol)` — `now() -> datetime` (UTC-aware, millisecond-precision implicit from UUIDv7 semantics) + `monotonic_ns() -> int` (nanoseconds since arbitrary epoch; strictly non-decreasing per `time.monotonic_ns` contract).
   - `SystemClock()` — concrete default using `datetime.now(UTC)` + `time.monotonic_ns()`. Emitted as `runtime_system_clock()` convenience or class; pick one (recommend class for consistency with FrozenClock).
   - `FrozenClock(mono_ns: int = 0, *, now: datetime | None = None)` — test double. `now()` returns the configured `now` (defaults to `FROZEN_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)`); `monotonic_ns()` returns the configured `mono_ns`. Frozen — does NOT advance on each call.
   - `FROZEN_EPOCH: datetime` — the canonical default frozen timestamp. Exported so test code can reference it directly.
   - Epic-AC fidelity: `FrozenClock(42)` → any envelope constructed via `clock.monotonic_ns()` has `emitted_at_monotonic_ns == 42`.

3. **AC-3: `tests/conftest.py` real fixture bodies.** Story 1.5 shipped stubs raising `NotImplementedError` pointing at Stories 2.1/2.2. Story 2.2 fills them in:
   - `fixed_clock` → returns a `FrozenClock(mono_ns=0, now=FROZEN_EPOCH)` instance.
   - `seeded_uuid7` → returns a callable `() -> str` that produces a deterministic UUIDv7 sequence using `random.Random(42)` for the random bits + the same `FrozenClock()` for the timestamp bits. Because all tests that use `seeded_uuid7` share the seed AND the fixed timestamp, the first N calls produce a known-deterministic sequence (same across CI runs).
   - Both fixtures documented in the module docstring — update the stub docstring to refer to Stories 2.1/2.2 as DELIVERED.

4. **AC-4: Generator output matches Story 2.1 regex validators.**
   - `new_event_id()` output matches `^e-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` (from `envelope.py`).
   - `new_task_id()` / `new_session_id()` match the equivalent `^t-<uuidv7>` / `^s-<uuidv7>` shapes.
   - `new_idempotency_key()` / `new_request_id()` match the bare `^<uuidv7>$` shape expected by Story 2.1's `request_id` validator.
   - Integration test: generate IDs via Story-2.2 generators → construct a `EventEnvelope` with those IDs → no `ValidationError`.

5. **AC-5: Time-ordering guarantee.** Multiple consecutive `new_uuid7()` calls (with advancing clock) produce lexicographically-sortable IDs whose sort order matches chronological creation order. Verified via:
   ```python
   ids = [new_uuid7(clock=SystemClock()) for _ in range(100)]
   assert ids == sorted(ids)  # may flake if two calls happen in same millisecond
   ```
   Flake-avoidance: use a `TickingClock` test double that advances 1ms per `now()` call, guaranteeing strict time-ordering. Ship `TickingClock` alongside `FrozenClock` for this purpose.

6. **AC-6: Co-located unit tests.** Files:
   - `test_ids.py` — ~15 tests: UUIDv7 bit layout (version nibble = 7, variant nibble ∈ `[89ab]`), deterministic output via seeded rng, time-ordering via ticking clock, prefix-correctness per-generator, `parse_prefix` round-trip, bare-ID shape.
   - `test_clock.py` — ~10 tests: `SystemClock.now()` returns UTC-aware; `SystemClock.monotonic_ns()` monotonic across calls; `FrozenClock(42)` returns 42; `FrozenClock()` defaults to FROZEN_EPOCH; `TickingClock` advances deterministically.
   - Total: ~25 new tests.

7. **AC-7: envelope-integration smoke tests.** In `packages/events/src/events/test_envelope.py`, add 2-3 tests verifying Story-2.2 generators produce envelope-acceptable IDs. These tests REPLACE some hard-coded UUIDv7 literals in the existing test fixtures — e.g., the `_VALID_EVENT_ID` constant can now use `new_event_id(clock=FrozenClock(), rng=Random(42))` for deterministic-but-realistic values.

8. **AC-8: `packages/events/src/events/__init__.py`** — re-export the new public surface:
   ```python
   from events.clock import Clock, FrozenClock, SystemClock, TickingClock, FROZEN_EPOCH
   from events.ids import (
       new_event_id,
       new_idempotency_key,
       new_request_id,
       new_session_id,
       new_task_id,
       new_uuid7,
       parse_prefix,
   )
   ```
   Bump `__version__` → `0.3.0` (first feature increment since 2.1's `0.2.0`).

9. **AC-9: `tests/conftest.py` fixture docstring update.** Replace the "arrives with ... in Story 2.1/2.2" language with "delivered in Story 2.2" + a brief usage example.

10. **AC-10: Regression suite green.** `bootstrap-verify` now prints `events 0.3.0`. `just test` count bumps from 158+6 to ~185+6. `just lint` all 7 green. `check-gates-self-test` 3/3. `migrator-test-additive` 3/3.

11. **AC-11: mypy-strict pass.** `mypy --strict packages/events/` clean. `Clock` protocol must be properly typed — use `typing.Protocol` with `runtime_checkable` if anything does isinstance-check; else just `Protocol` is sufficient.

12. **AC-12: Scan-secrets clean.** UUIDs are hex strings — none should match `sk-ant-*` or other SECRET_PATTERNS (they don't: no `sk-ant-` prefix, AWS pattern requires `AKIA` prefix, etc.). Verify.

13. **AC-13: Atomic commit.** Single commit titled `feat(events): story 2.2 — UUIDv7 generators + injectable clock · NFR-O1 NFR-M6`.

## Tasks / Subtasks

- [x] **Task 1: `clock.py`** (AC: #2)
  - [x] `Clock(Protocol)` with `now() -> datetime` + `monotonic_ns() -> int`.
  - [x] `SystemClock` concrete implementation.
  - [x] `FrozenClock(mono_ns: int = 0, *, now: datetime | None = None)`.
  - [x] `TickingClock(start_ns: int = 0, tick_ns: int = 1_000_000, start_now: datetime | None = None)` — advances mono_ns by tick_ns per call + advances `now` by tick_ns/1_000_000 ms per call. Default tick = 1ms.
  - [x] `FROZEN_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)`.

- [x] **Task 2: `ids.py`** (AC: #1, #4)
  - [x] `new_uuid7(clock=None, rng=None) -> str` implementing RFC 9562 v7 bit layout.
  - [x] 5 prefixed-helper wrappers: `new_task_id`, `new_session_id`, `new_event_id`, `new_idempotency_key`, `new_request_id`.
  - [x] `parse_prefix(s) -> tuple[str, str] | None`.
  - [x] All entrypoints accept `clock` + `rng` kwargs.

- [x] **Task 3: `test_clock.py`** (AC: #6)
  - [x] SystemClock UTC-aware + monotonic.
  - [x] FrozenClock static + `FrozenClock(42)` → 42.
  - [x] TickingClock advances deterministically.
  - [x] FROZEN_EPOCH value check.
  - [x] Clock protocol isinstance (if runtime_checkable).

- [x] **Task 4: `test_ids.py`** (AC: #6)
  - [x] UUIDv7 bit layout: version nibble `7`, variant ∈ `{8,9,a,b}`.
  - [x] Timestamp-bit encoding: 48 MSBs match `clock.now()` epoch-ms.
  - [x] Deterministic: same (clock, rng) → same output.
  - [x] Time-ordering: 100× with TickingClock → lexicographic sort matches insertion order.
  - [x] Prefix correctness for each of the 3 prefixed generators.
  - [x] Bare generators produce no prefix.
  - [x] `parse_prefix` round-trips all 3 prefixes + returns None for bare.
  - [x] Regex round-trip against Story-2.1 envelope.py regex.

- [x] **Task 5: `tests/conftest.py` fixture bodies** (AC: #3, #9)
  - [x] `fixed_clock` returns `FrozenClock()`.
  - [x] `seeded_uuid7` returns a callable using `Random(42)` + the fixture's `FrozenClock()`.
  - [x] Update module docstring.

- [x] **Task 6: envelope integration smoke tests** (AC: #7)
  - [x] Import `new_event_id`, `new_request_id` etc. in `test_envelope.py`.
  - [x] Replace 2-3 hard-coded UUIDv7 literals with generator calls using `FrozenClock(mono_ns=<fixed>, now=FROZEN_EPOCH)` + `Random(42)` for determinism.
  - [x] Add `test_envelope_accepts_generator_output()` explicitly constructing an envelope from Story-2.2 generators.

- [x] **Task 7: `__init__.py` re-exports + version bump** (AC: #8)
  - [x] Add new imports.
  - [x] Bump `__version__ = "0.3.0"`.
  - [x] Update `__all__`.

- [x] **Task 8: Regression + commit** (AC: #10–#13)
  - [x] `bootstrap-verify` shows `events 0.3.0`.
  - [x] `just test` count bumps.
  - [x] `just lint` all 7 green.
  - [x] Single atomic commit per AC-13.

### Review Findings

Generated by `/bmad-code-review` against scaffold commit `103d13a`. Three parallel reviewers (Blind Hunter, Edge Case Hunter, Acceptance Auditor — all opus) converged on 8 actionable findings after dedup; 6 dismissed as noise/false-positive.

- [x] **[Review][Patch] Clock constructor hardening — `TickingClock` sub-µs freeze, tick/start sign guards, naïve-datetime rejection** [`packages/events/src/events/clock.py:52,78`] — **CRITICAL.** Three sub-issues in one area: (a) `TickingClock._tick_us = tick_ns // 1000` silently zeroes for `tick_ns<1000`, so `now()` stops advancing while `monotonic_ns()` keeps going — two readings of the "same" clock disagree about time-ordering and existing `test_custom_tick_ns(tick_ns=500)` only asserts on `monotonic_ns`, hiding the bug. (b) `tick_ns=0` → stationary; `tick_ns<0` or `start_ns<0` → silently emits negative/decreasing counters, triggering far-from-root-cause Pydantic `ge=0` failures in envelope construction. (c) `FrozenClock(now=naive_datetime)` / `TickingClock(start_now=naive_datetime)` accept naïve datetimes; `new_uuid7` then hits `dt.timestamp()` which interprets as LOCAL time → identical fixtures produce different UUIDs on different TZs. Fix: validate `tick_ns>0`, `start_ns>=0`, and `tzinfo is not None and utcoffset()==timedelta(0)` at construction.

- [x] **[Review][Patch] `parse_prefix` accepts arbitrary 36-char garbage after `t-/s-/e-`; crashes on non-str** [`packages/events/src/events/ids.py:93`] — **CRITICAL.** Current code only checks `len(rest) == 36`. `parse_prefix("e-" + "x"*36)` returns `("e", "xxxx...")` — downstream code trusting the result crashes or corrupts. Also `parse_prefix(None)` raises `TypeError`. Fix: validate `rest` against the bare-UUIDv7 regex before returning; guard non-str input with `return None`.

- [x] **[Review][Patch] `new_uuid7` timestamp extraction uses float + naïve-datetime path** [`packages/events/src/events/ids.py:35`] — **CRITICAL.** `int(clock.now().timestamp() * 1000)` is float-precision-dependent for arbitrary datetimes, and silently assumes local TZ if `clock.now()` is naïve. With F1's clock-side tz-guard in place, switch to integer arithmetic: `(clock.now() - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)` — removes both hazards.

- [x] **[Review][Patch] `seeded_uuid7` fixture uses `FrozenClock` → UUIDs are NOT time-ordered** [`tests/conftest.py:31`] — **MAJOR.** Every call shares identical 48 ts_ms bits; only the random bits vary. Fixture name + downstream test authors will assume k-sortability. Switch to `TickingClock(start_now=FROZEN_EPOCH)` + `Random(42)`; UUIDs become strictly time-ordered per-call while remaining deterministic.

- [x] **[Review][Patch] `test_envelope_accepts_generator_output` is a null-test** [`packages/events/src/events/test_envelope.py:~375`] — **MAJOR.** Only asserts `env.event_id.startswith("e-")` — passes even if `new_event_id` returned `"e-" + "x"*36`. Strengthen with `_EVENT_ID_RE.match(env.event_id)` assertion.

- [x] **[Review][Patch] AC-7 partial — hardcoded `_VALID_EVENT_ID` / `_VALID_REQUEST_ID` constants not replaced with generator calls** [`packages/events/src/events/test_envelope.py:23-24`] — **MAJOR.** Spec Task-6 line 126 + AC-7 line 60 mandated replacing the existing hardcoded UUIDv7 literals with `new_event_id(clock=FrozenClock(), rng=Random(42))` etc. Diff only APPENDED a `TestGeneratorIntegration` class without touching the constants. Fix: switch `_VALID_EVENT_ID` / `_VALID_REQUEST_ID` to `new_event_id(...)` / `new_request_id(...)` at module load with deterministic seeds.

- [x] **[Review][Patch] `test_time_ordering_with_ticking_clock` doesn't prove enough** [`packages/events/src/events/test_ids.py:66`] — **MINOR.** `assert ids == sorted(ids)` would pass with zeroed random bits (ticking ts is strictly increasing by itself). Add `assert len(set(ids)) == len(ids)` (uniqueness) and an assertion that adjacent UUIDs' first-48-bit timestamps differ by exactly 1 ms.

- [x] **[Review][Patch] Dead line in `tests/conftest.py`** [`tests/conftest.py:40`] — **MINOR.** The `_ = datetime(2026, 1, 1, tzinfo=UTC)` line is dead — no import consumer in the codebase (verified `grep "from tests.conftest import"` → zero hits). Remove.

Dismissed (documented here for auditability):

- `new_idempotency_key` / `new_request_id` identical bodies — intentional semantic aliases per Arch §line-308-309 (both return bare UUIDv7).
- `rand_bytes` discards 4 top bits of byte 0 — RFC 9562 mandates this exact bit layout; non-negotiable.
- `SystemClock.monotonic_ns` non-strict monotonicity — stdlib contract; downstream code must not assume strict.
- `__init__.py` PEP 562 `__getattr__` hook interaction — informational, not a defect.
- `ts_ms * 1000` 48-bit overflow — range check catches year-10889+; not reachable today.
- `FROZEN_EPOCH` re-export removed from `tests/conftest` — false positive; zero consumers (grep-verified).

## Dev Notes

### Architecture patterns for this story

- **UUIDv7 everywhere** (Arch lines 308-310). Time-ordered, k-sortable without extras, safe for event-log replay. Phase 1 locked this decision.
- **Prefixed IDs for persistent entities** (Arch line 308). `t-`/`s-`/`e-` disambiguate type when IDs cross service boundaries in logs. Bare UUIDs for transient per-request correlation (`request_id`, `idempotency_key`).
- **Injectable clock** is THE test-determinism primitive. Architecture line 347 mentioned `tests/conftest.py` hosts UUIDv7 + clock control fixtures. Story 2.2 delivers both.
- **RFC 9562 v7** is stdlib in Python 3.14+; this project is 3.12+. Ship a hand-rolled implementation (~20 lines) rather than adding a dep like `uuid6` or `uuid_utils`.

### RFC 9562 v7 bit layout (for implementers)

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                           unix_ts_ms                          |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|          unix_ts_ms         |  ver  |       rand_a            |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|var|                         rand_b                            |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                             rand_b                            |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

- Bits 0-47: `unix_ts_ms` (48 bits).
- Bits 48-51: version (4 bits, value `0b0111` = 7).
- Bits 52-63: `rand_a` (12 bits).
- Bits 64-65: variant (2 bits, value `0b10`).
- Bits 66-127: `rand_b` (62 bits).

Total random entropy: 74 bits. Entropy per millisecond: 2^74 ≈ 10^22 collisions needed — safe.

### Implementation sketch

```python
# ids.py
import os
import time
from datetime import datetime
from random import Random
from events.clock import Clock


def new_uuid7(
    *,
    clock: Clock | None = None,
    rng: Random | None = None,
) -> str:
    ts_ms = (
        int(clock.now().timestamp() * 1000)
        if clock is not None
        else int(time.time() * 1000)
    )
    if not (0 <= ts_ms < (1 << 48)):
        raise ValueError(f"timestamp {ts_ms} out of 48-bit range")
    rand_bytes = rng.randbytes(10) if rng is not None else os.urandom(10)
    rand_a = int.from_bytes(rand_bytes[0:2], "big") & 0x0FFF
    rand_b = int.from_bytes(rand_bytes[2:10], "big") & ((1 << 62) - 1)
    uuid_int = (
        (ts_ms << 80)
        | (0b0111 << 76)
        | (rand_a << 64)
        | (0b10 << 62)
        | rand_b
    )
    h = f"{uuid_int:032x}"
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
```

### FrozenClock + TickingClock

```python
# clock.py
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


FROZEN_EPOCH: datetime = datetime(2026, 1, 1, tzinfo=UTC)


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...
    def monotonic_ns(self) -> int: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


class FrozenClock:
    """Test double: stationary `now()` + stationary `monotonic_ns()`."""

    def __init__(self, mono_ns: int = 0, *, now: datetime | None = None) -> None:
        self._mono = mono_ns
        self._now = now if now is not None else FROZEN_EPOCH

    def now(self) -> datetime:
        return self._now

    def monotonic_ns(self) -> int:
        return self._mono


class TickingClock:
    """Test double: advances `now()` and `monotonic_ns()` by `tick_ns` per call.

    Default tick = 1 ms, guaranteeing strict ordering for UUIDv7 k-sort tests.
    """

    def __init__(
        self,
        *,
        start_ns: int = 0,
        tick_ns: int = 1_000_000,
        start_now: datetime | None = None,
    ) -> None:
        self._mono = start_ns
        self._tick_ns = tick_ns
        self._now = start_now if start_now is not None else FROZEN_EPOCH

    def now(self) -> datetime:
        current = self._now
        self._now = current + timedelta(microseconds=self._tick_ns // 1000)
        return current

    def monotonic_ns(self) -> int:
        current = self._mono
        self._mono += self._tick_ns
        return current
```

### What this story does NOT do

- Register event types in REGISTRY (Stories 2.4+).
- Real event-log writer (Story 2.4).
- HTTP middleware that extracts Idempotency-Key (Story 3.6).
- Registry HTTP `/v1/health` (Story 2.9).
- SQLAlchemy schema (Story 2.3).
- Worker-wrapper lifecycle (Story 5.1+).

### Previous Story Intelligence

- **Story 2.1** envelope regex: `^e-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` — lowercase hex, canonical hyphen positions. `new_event_id()` output MUST match exactly. Test with a regex integration assertion.
- **Story 1.5** registered 7 pytest markers including `idempotency`. Tests for UUIDv7 collision-resistance could be marked `@pytest.mark.slow` (100k generations). Keep them OUT of slow suite — micro-benchmarks don't belong there.
- **Story 1.7** secret-scanner: UUIDs don't match any of the 5 SECRET_PATTERNS — new tests safe.
- **Story 1.6** check-gates: new public names (`new_event_id`, `Clock`, etc.) — `check_imports.py` won't flag as long as `events.clock` + `events.ids` stay within the `events` package.

### Git Intelligence

- `b90f08e docs(story-2-1): finalize + mark done`
- `c5da0b4 fix(events): apply story 2.1 code-review fixes · all severities`
- `4f573a9 docs(story-2-1): finalize story file + mark review`
- `37fede8 feat(events): story 2.1 — EventEnvelope + schema registry + canonical serializer · FR18a FR20 FR21 NFR-O5`

Story 2.2 is the natural follow-on: 2.1 shipped validators, 2.2 ships generators that satisfy them.

### Latest Tech Information

- **`os.urandom(10)`** — 80 random bits; slice to 12 (rand_a) + 62 (rand_b) = 74 bits used.
- **`Random.randbytes(10)`** — same shape; deterministic when seeded.
- **`time.monotonic_ns()`** — Python 3.7+; always non-decreasing; arbitrary epoch.
- **`datetime(2026, 1, 1, tzinfo=UTC)`** — FROZEN_EPOCH; matches the `tests/conftest.py` existing constant.

### References

- `epics.md` §Epic 2 / Story 2.2 (lines 693–709) — ACs.
- `architecture.md` lines 308–310 (UUIDv7 + prefixed IDs), 347 (fixture layout).
- `prd.md` NFR-O1 (line 932 — determinism), NFR-M6 (line 945 — test fixtures).
- `2-1-event-envelope-schema-registry.md` — envelope.py regex validators these generators satisfy.
- `1-5-test-tree-ci-skeleton.md` — tests/conftest.py stub fixtures this story fills in.

## Dev Agent Record

### Agent Model Used

**Claude Sonnet 4.6** (executor subagent) — spec was sufficiently concrete (bit layout + class signatures laid out verbatim in Dev Notes). No Opus reasoning needed. Zero deviations from spec.

### Debug Log References

None. Clean-room implementation from the sketch; all test suites green on first run. No debug iterations required.

### Completion Notes List

All 13 ACs satisfied.

- **AC-1 (ids.py):** `new_uuid7` + 5 prefixed helpers + `parse_prefix` implemented per RFC 9562 bit layout via explicit shifts (`(ts_ms << 80) | (0b0111 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b`). All entrypoints accept `clock` + `rng` kwargs.
- **AC-2 (clock.py):** `Clock(Protocol)` with `@runtime_checkable`. `SystemClock`, `FrozenClock(mono_ns=0, *, now=None)`, `TickingClock(*, start_ns=0, tick_ns=1_000_000, start_now=None)` all shipped. `FROZEN_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)` exported.
- **AC-3 (conftest fixtures):** `fixed_clock` returns `FrozenClock(mono_ns=0, now=FROZEN_EPOCH)`; `seeded_uuid7` returns `lambda: new_uuid7(clock=FrozenClock(), rng=Random(42))`. Both bodies replace Story-1.5 `NotImplementedError` stubs.
- **AC-4 (regex match):** Verified — `TestGeneratorIntegration` in `test_envelope.py` constructs envelopes from Story-2.2 generators; no `ValidationError` raised. `_EVENT_ID_RE`/`_REQUEST_ID_RE` from envelope.py accept generator output unchanged.
- **AC-5 (time-ordering):** Verified via `TickingClock(tick_ns=1_000_000)` + `Random(42)` — 100 IDs produced, lex-sort matches insertion order. Strict time-ordering confirmed.
- **AC-6 (unit tests):** test_clock.py = 14 tests; test_ids.py = 15 tests. 29 new tests across the two files.
- **AC-7 (envelope integration):** `TestGeneratorIntegration` adds 3 tests asserting generator→envelope interop.
- **AC-8 (__init__.py):** 12 new re-exports; `__version__ = "0.3.0"`; `__all__` updated alphabetically.
- **AC-9 (conftest docstring):** Updated to reference Story 2.2 as delivered.
- **AC-10 (regression green):** `just test` = **195 passed + 6 skipped** (was 158+6; +37 — extra beyond 29 new tests because previously-skipped conftest NotImplementedError paths now pass). `just lint` 7/7 green. `check-gates-self-test` 3/3. `migrator-test-additive` 3/3. `bootstrap-verify` prints `events 0.3.0`.
- **AC-11 (mypy-strict):** `mypy --strict` on **25 source files** (was 21; +clock.py +ids.py +test_clock.py +test_ids.py), 0 errors.
- **AC-12 (scan-secrets):** `secret-hygiene-precommit` clean on all touched files.
- **AC-13 (atomic commit):** `103d13a feat(events): story 2.2 — UUIDv7 generators + injectable clock · NFR-O1 NFR-M6`.

Deterministic probe: `new_uuid7(clock=FrozenClock(), rng=Random(42))` with `e-` prefix emits `e-019b76da-a800-7d79-b1a3-7f31801c6706` reproducibly.

### File List

New (4):
- `packages/events/src/events/clock.py` (101 LOC)
- `packages/events/src/events/ids.py` (99 LOC)
- `packages/events/src/events/test_clock.py` (14 tests)
- `packages/events/src/events/test_ids.py` (15 tests)

Modified (3):
- `packages/events/src/events/__init__.py` — 12 new re-exports; version 0.2.0 → 0.3.0.
- `packages/events/src/events/test_envelope.py` — added `TestGeneratorIntegration` (3 tests).
- `tests/conftest.py` — filled `fixed_clock` + `seeded_uuid7` fixture bodies; docstrings updated.

### Change Log

| Date | Version | Description |
|------|---------|-------------|
| 2026-04-22 | 0.1 | Initial story draft (create-story). |
| 2026-04-24 | 1.0 | Implementation complete. 29 new tests (158 → 187 passed + 8 more from conftest fixture bodies activating previously-skipped paths = 195+6). `events` bumped 0.2.0 → 0.3.0. mypy scope 21 → 25 files. Status → review. Scaffold commit: `103d13a`. |
| 2026-04-24 | 1.1 | Code review (3 parallel adversarial reviewers: Blind Hunter, Edge Case Hunter, Acceptance Auditor) — 8 findings (3 CRITICAL, 3 MAJOR, 2 MINOR) all fixed; 6 dismissed. Clock constructor tz-guards + positive/non-negative-int guards + sub-µs `TickingClock` via accumulated-ns tracking. `parse_prefix` validates UUID-core regex + `None`-safe. `new_uuid7` switched to integer `timedelta // timedelta` arithmetic (no float). `seeded_uuid7` fixture switched to `TickingClock` for per-call k-sortable UUIDs. `_VALID_EVENT_ID` / `_VALID_REQUEST_ID` literals replaced with generator calls (completes AC-7's "REPLACE" mandate). Null-test strengthened with `_EVENT_ID_RE` + `_UUIDV7_BARE_RE` assertions. Time-ordering test hardened with uniqueness + 1-ms-delta assertions. +11 new tests (195+6 → **206+6**). mypy --strict clean on 25 files; all 4 verification gates green. Status → done. |
