"""Tests for registry_state.adapters.event_log — Story 2.4 AC-12.

Test classes:
- TestCurrentDayPath        (~4 tests) — free function, UTC-only guard.
- TestEventLogWriterRoundTrip (~5 tests) — append + read_log_lines.
- TestDailyRollover         (~4 tests) — per-day file rollover.
- TestDurability            (~3 tests) — fdatasync behaviour, idempotent close.
- TestRecover               (~5 tests) — backward-chunk scan + ftruncate.
- TestDirectoryCreation     (~1 test) — mkdir(parents=True, exist_ok=True).

All tests use ``tmp_path`` for file I/O.  The ``fixed_clock`` and
``seeded_uuid7`` fixtures are defined here with identical logic to
``tests/conftest.py`` (Story 2.2 / AC-13 — no new conftest file added;
these are the same fixtures re-declared locally so co-located tests can
run standalone without depending on rootdir conftest traversal).

Async tests use ``pytest.mark.asyncio`` with the project-wide ``asyncio_mode =
"strict"`` setting from ``pyproject.toml``.

Note on payload round-trip: envelopes use *dict* payloads (not BaseModel
instances) so that ``to_canonical_json`` + ``from_canonical_json`` produces
byte-identical + equality-preserving round-trips.  When payload is a
BaseModel, ``model_dump(mode='python')`` serialises it as a nested dict; the
round-trip produces an equivalent ``_FrozenDict``, which is NOT equal to the
original BaseModel instance.  Using ``{"value": "..."}`` dicts avoids this.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from random import Random

import pytest
from events import (
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    FrozenClock,
    TickingClock,
    new_event_id,
    new_uuid7,
    to_canonical_json,
)
from events.schema_registry import register, unregister_all
from pydantic import BaseModel

from registry_state.adapters.event_log import (
    EventLogWriter,
    current_day_path,
    read_log_lines,
    recover_all_logs,
)
from registry_state.domain.event_types import ensure_registered

# ---------------------------------------------------------------------------
# Local fixtures — mirror tests/conftest.py exactly (AC-13: no new conftest)
# ---------------------------------------------------------------------------


@pytest.fixture
def fixed_clock() -> FrozenClock:
    """Stationary clock at FROZEN_EPOCH with mono_ns=0 (mirrors tests/conftest.py)."""
    return FrozenClock(mono_ns=0, now=FROZEN_EPOCH)


@pytest.fixture
def seeded_uuid7() -> Callable[[], str]:
    """Deterministic UUIDv7 factory (mirrors tests/conftest.py)."""
    rng = Random(42)
    clock = TickingClock(start_now=FROZEN_EPOCH)
    return lambda: new_uuid7(clock=clock, rng=rng)


# ---------------------------------------------------------------------------
# Test payload model + registry management
# ---------------------------------------------------------------------------


class _SimplePayload(BaseModel):
    value: str


@pytest.fixture(autouse=True)
def _clean_registry() -> Generator[None, None, None]:
    """Isolate schema-registry state between tests.

    Story 8.7.5 PP1: teardown MUST restore canonical state via
    ensure_registered() — otherwise consumer tests (test_decisions,
    test_approvals, etc.) that run AFTER this file in pytest's
    collection order will fail with EventSchemaUnknown when
    instantiating approval events.
    """
    unregister_all()
    register("task.created", "1.0.0", _SimplePayload)
    yield
    unregister_all()
    ensure_registered()  # PP1 — restore canonical state for sibling tests


# ---------------------------------------------------------------------------
# Helper: build a minimal valid EventEnvelope with dict payload
# ---------------------------------------------------------------------------

_ACTOR = Actor(kind="system", id="test")


_DEFAULT_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000000"


def _make_envelope(
    clock: FrozenClock | None = None,
    value: str = "hello",
    parent_event_id: str | None = None,
    trace_id: str | None = None,
    request_id: str | None = None,
    mono_seed: int = 0,
) -> EventEnvelope:
    """Build a minimal valid EventEnvelope for testing.

    Uses a *dict* payload so that ``to_canonical_json`` + ``from_canonical_json``
    round-trips produce equal envelopes.

    Story 9.7: trace_id is now REQUIRED. Default to _DEFAULT_TRACE_ID when
    the caller doesn't supply one.
    """
    rng = Random(mono_seed)
    clk = clock or FrozenClock(mono_ns=mono_seed, now=FROZEN_EPOCH)
    eid = new_event_id(clock=clk, rng=rng)
    rid = request_id or new_uuid7(clock=clk, rng=rng)
    return EventEnvelope(
        event_id=eid,
        schema_version="1.0.0",
        type="task.created",  # noqa: EVT001 test-only fixture envelope
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload={"value": value},
        parent_event_id=parent_event_id,
        trace_id=trace_id if trace_id is not None else _DEFAULT_TRACE_ID,
        request_id=rid,
    )


# ===========================================================================
# TestCurrentDayPath
# ===========================================================================


class TestCurrentDayPath:
    def test_returns_correct_path_for_utc_datetime(self, tmp_path: Path) -> None:
        now = datetime(2026, 4, 24, 15, 30, 0, tzinfo=UTC)
        path = current_day_path(tmp_path, now)
        assert path == tmp_path / "2026-04-24.jsonl"

    def test_raises_for_naive_datetime(self, tmp_path: Path) -> None:
        naive = datetime(2026, 4, 24, 15, 30, 0)  # no tzinfo
        with pytest.raises(ValueError, match="UTC-aware"):
            current_day_path(tmp_path, naive)

    def test_raises_for_non_utc_tzinfo(self, tmp_path: Path) -> None:
        eastern = datetime(2026, 4, 24, 15, 30, 0, tzinfo=timezone(timedelta(hours=-5)))
        with pytest.raises(ValueError, match="UTC-aware"):
            current_day_path(tmp_path, eastern)

    def test_midnight_boundary_adjacent_files(self, tmp_path: Path) -> None:
        # 23:59:59.999Z → 2026-04-24
        before_midnight = datetime(2026, 4, 24, 23, 59, 59, 999000, tzinfo=UTC)
        # 00:00:00.000Z → 2026-04-25
        after_midnight = datetime(2026, 4, 25, 0, 0, 0, 0, tzinfo=UTC)

        path_before = current_day_path(tmp_path, before_midnight)
        path_after = current_day_path(tmp_path, after_midnight)

        assert path_before == tmp_path / "2026-04-24.jsonl"
        assert path_after == tmp_path / "2026-04-25.jsonl"
        assert path_before != path_after


# ===========================================================================
# TestEventLogWriterRoundTrip
# ===========================================================================


class TestEventLogWriterRoundTrip:
    @pytest.mark.asyncio
    async def test_single_append_round_trips(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        env = _make_envelope(clock=fixed_clock)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        await writer.append(env)
        await writer.close()

        path = current_day_path(tmp_path, fixed_clock.now())
        recovered = list(read_log_lines(path))
        assert len(recovered) == 1
        assert recovered[0] == env

    @pytest.mark.asyncio
    async def test_100_envelope_sequence_round_trips(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """100 envelopes written via seeded rng all round-trip with correct order."""
        rng = Random(42)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        envelopes = []
        for i in range(100):
            clk_i = FrozenClock(mono_ns=i, now=FROZEN_EPOCH)
            env = EventEnvelope(
                event_id=new_event_id(clock=clk_i, rng=rng),
                schema_version="1.0.0",
                type="task.created",  # noqa: EVT001 test-only fixture envelope
                emitted_at=FROZEN_EPOCH,
                emitted_at_monotonic_ns=i,
                actor=_ACTOR,
                payload={"value": f"item-{i}"},
                trace_id="01917e5c-a7d1-7000-8abc-000000000000",
                request_id=new_uuid7(clock=clk_i, rng=rng),
            )
            envelopes.append(env)
            await writer.append(env)
        await writer.close()

        path = current_day_path(tmp_path, FROZEN_EPOCH)
        recovered = list(read_log_lines(path))
        assert len(recovered) == 100
        assert recovered == envelopes

    @pytest.mark.asyncio
    async def test_optional_fields_round_trip(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        rng = Random(99)
        parent_id = new_event_id(clock=fixed_clock, rng=rng)
        task_id = new_uuid7(clock=fixed_clock, rng=rng)
        session_id = new_uuid7(clock=fixed_clock, rng=rng)
        env = EventEnvelope(
            event_id=new_event_id(clock=fixed_clock, rng=rng),
            schema_version="1.0.0",
            type="task.created",  # noqa: EVT001 test-only fixture envelope
            emitted_at=fixed_clock.now(),
            emitted_at_monotonic_ns=fixed_clock.monotonic_ns(),
            actor=_ACTOR,
            payload={"value": "with-optional"},
            parent_event_id=parent_id,
            trace_id=task_id,
            request_id=session_id,
        )
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        await writer.append(env)
        await writer.close()

        path = current_day_path(tmp_path, fixed_clock.now())
        recovered = list(read_log_lines(path))
        assert len(recovered) == 1
        assert recovered[0] == env
        assert recovered[0].parent_event_id == parent_id
        assert recovered[0].trace_id == task_id

    @pytest.mark.asyncio
    async def test_each_line_ends_with_exactly_one_newline(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        env = _make_envelope(clock=fixed_clock)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        await writer.append(env)
        await writer.close()

        path = current_day_path(tmp_path, fixed_clock.now())
        raw = path.read_bytes()
        # Exactly one trailing newline, no CR, no BOM
        assert raw.endswith(b"\n")
        assert raw.count(b"\n") == 1
        assert b"\r" not in raw
        assert not raw.startswith(b"\xef\xbb\xbf")  # no BOM

    @pytest.mark.asyncio
    async def test_canonical_json_bytes_appear_verbatim(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        env = _make_envelope(clock=fixed_clock)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        await writer.append(env)
        await writer.close()

        path = current_day_path(tmp_path, fixed_clock.now())
        raw = path.read_bytes()
        expected_bytes = to_canonical_json(env)
        # The file is exactly canonical-json + single newline
        assert raw == expected_bytes + b"\n"


# ===========================================================================
# TestDailyRollover
# ===========================================================================


class TestDailyRollover:
    @pytest.mark.asyncio
    async def test_two_appends_same_day_same_file(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        env1 = _make_envelope(clock=fixed_clock, value="first", mono_seed=1)
        env2 = _make_envelope(clock=fixed_clock, value="second", mono_seed=2)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        await writer.append(env1)
        await writer.append(env2)
        await writer.close()

        path = current_day_path(tmp_path, fixed_clock.now())
        recovered = list(read_log_lines(path))
        assert len(recovered) == 2

    @pytest.mark.asyncio
    async def test_append_across_midnight_opens_new_file(self, tmp_path: Path) -> None:
        # TickingClock advancing 1 day per call: first now() = 2026-01-01, second = 2026-01-02
        day_ns = 86_400 * 1_000_000_000
        clock = TickingClock(start_now=FROZEN_EPOCH, tick_ns=day_ns)
        writer = EventLogWriter(base_dir=tmp_path, clock=clock)

        rng = Random(7)
        clk0 = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
        clk1 = FrozenClock(mono_ns=1, now=FROZEN_EPOCH)

        env1 = EventEnvelope(
            event_id=new_event_id(clock=clk0, rng=rng),
            schema_version="1.0.0",
            type="task.created",  # noqa: EVT001 test-only fixture envelope
            emitted_at=FROZEN_EPOCH,
            emitted_at_monotonic_ns=0,
            actor=_ACTOR,
            payload={"value": "day0"},
            trace_id="01917e5c-a7d1-7000-8abc-000000000000",
            request_id=new_uuid7(clock=clk0, rng=rng),
        )
        await writer.append(env1)

        # Second append: clock.now() advances to 2026-01-02
        env2 = EventEnvelope(
            event_id=new_event_id(clock=clk1, rng=rng),
            schema_version="1.0.0",
            type="task.created",  # noqa: EVT001 test-only fixture envelope
            emitted_at=FROZEN_EPOCH,
            emitted_at_monotonic_ns=1,
            actor=_ACTOR,
            payload={"value": "day1"},
            trace_id="01917e5c-a7d1-7000-8abc-000000000000",
            request_id=new_uuid7(clock=clk1, rng=rng),
        )
        await writer.append(env2)
        await writer.close()

        day0_path = tmp_path / "2026-01-01.jsonl"
        day1_path = tmp_path / "2026-01-02.jsonl"
        assert day0_path.exists(), "day-0 file missing"
        assert day1_path.exists(), "day-1 file missing"
        assert len(list(read_log_lines(day0_path))) == 1
        assert len(list(read_log_lines(day1_path))) == 1

    @pytest.mark.asyncio
    async def test_ticking_clock_1ms_stays_one_file(self, tmp_path: Path) -> None:
        # 1 ms ticks — all appends land in 2026-01-01 (no midnight crossing)
        clock = TickingClock(start_now=FROZEN_EPOCH, tick_ns=1_000_000)
        writer = EventLogWriter(base_dir=tmp_path, clock=clock)
        rng = Random(11)
        for i in range(5):
            env = EventEnvelope(
                event_id=new_event_id(clock=FrozenClock(mono_ns=i, now=FROZEN_EPOCH), rng=rng),
                schema_version="1.0.0",
                type="task.created",  # noqa: EVT001 test-only fixture envelope
                emitted_at=FROZEN_EPOCH,
                emitted_at_monotonic_ns=i,
                actor=_ACTOR,
                payload={"value": f"ms-{i}"},
                trace_id="01917e5c-a7d1-7000-8abc-000000000000",
                request_id=new_uuid7(clock=FrozenClock(mono_ns=i + 100, now=FROZEN_EPOCH), rng=rng),
            )
            await writer.append(env)
        await writer.close()

        jsonl_files = list(tmp_path.glob("*.jsonl"))
        assert len(jsonl_files) == 1
        assert jsonl_files[0].name == "2026-01-01.jsonl"

    @pytest.mark.asyncio
    async def test_file_naming_matches_yyyy_mm_dd_pattern(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        env = _make_envelope(clock=fixed_clock)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        await writer.append(env)
        await writer.close()

        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) == 1
        # FROZEN_EPOCH is 2026-01-01
        assert files[0].name == "2026-01-01.jsonl"


# ===========================================================================
# TestDurability
# ===========================================================================


class TestDurability:
    @pytest.mark.asyncio
    async def test_file_content_visible_immediately_after_append(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """After append() returns, content is readable by a fresh open() call.

        Observable proxy for fdatasync: data visible to a new reader immediately
        (not stuck in an unflushed OS write-back cache).
        """
        env = _make_envelope(clock=fixed_clock)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        await writer.append(env)

        # Do NOT call close() — verify content is readable while writer is open
        path = current_day_path(tmp_path, fixed_clock.now())
        raw = path.read_bytes()
        assert raw == to_canonical_json(env) + b"\n"

        await writer.close()

    @pytest.mark.asyncio
    async def test_envelope_recoverable_after_simulated_crash(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """Simulate hard kill after append: data survives and is re-readable."""
        env = _make_envelope(clock=fixed_clock)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        await writer.append(env)
        # Simulate crash: skip close(), create fresh reader
        path = current_day_path(tmp_path, fixed_clock.now())
        recovered = list(read_log_lines(path))
        assert len(recovered) == 1
        assert recovered[0] == env

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self, tmp_path: Path, fixed_clock: FrozenClock) -> None:
        env = _make_envelope(clock=fixed_clock)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        await writer.append(env)
        await writer.close()
        # Second close must not raise
        await writer.close()


# ===========================================================================
# TestRecover
# ===========================================================================


class TestRecover:
    @pytest.mark.asyncio
    async def test_recover_empty_file_returns_zero(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        path = current_day_path(tmp_path, fixed_clock.now())
        path.write_bytes(b"")
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        truncated = await writer.recover()
        assert truncated == 0

    @pytest.mark.asyncio
    async def test_recover_complete_lines_returns_zero(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        content = b'{"key":"val1"}\n{"key":"val2"}\n'
        path = current_day_path(tmp_path, fixed_clock.now())
        path.write_bytes(content)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        truncated = await writer.recover()
        assert truncated == 0
        assert path.read_bytes() == content

    @pytest.mark.asyncio
    async def test_recover_partial_tail_truncates(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        complete = b'{"key":"val1"}\n'
        partial = b'{"key":"partial-no-newline'
        path = current_day_path(tmp_path, fixed_clock.now())
        path.write_bytes(complete + partial)

        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        truncated = await writer.recover()

        assert truncated == len(partial)
        assert path.read_bytes() == complete

    @pytest.mark.asyncio
    async def test_recover_no_newline_truncates_to_zero(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        content = b"no-newline-at-all-so-entire-file-is-partial"
        path = current_day_path(tmp_path, fixed_clock.now())
        path.write_bytes(content)

        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        truncated = await writer.recover()

        assert truncated == len(content)
        assert path.read_bytes() == b""

    @pytest.mark.asyncio
    async def test_recover_nonexistent_file_returns_zero(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        # No file created — should return 0 without raising
        truncated = await writer.recover()
        assert truncated == 0


# ===========================================================================
# TestDirectoryCreation
# ===========================================================================


class TestDirectoryCreation:
    def test_constructor_creates_missing_directory_tree(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        nested = tmp_path / "a" / "b" / "c"
        assert not nested.exists()
        _writer = EventLogWriter(base_dir=nested, clock=fixed_clock)
        assert nested.is_dir()


# ===========================================================================
# TestShortWriteAndPoison — F1, F2, F9 hardening
# ===========================================================================


class TestShortWriteAndPoison:
    @pytest.mark.asyncio
    async def test_append_after_close_raises(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """Once close() has run, further append() calls raise RuntimeError."""
        env = _make_envelope(clock=fixed_clock)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        await writer.append(env)
        await writer.close()

        with pytest.raises(RuntimeError, match="closed"):
            await writer.append(env)

    @pytest.mark.asyncio
    async def test_poisoned_writer_rejects_append_until_recover(
        self,
        tmp_path: Path,
        fixed_clock: FrozenClock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failed write poisons the writer; recover() cures it.

        1. Monkey-patch os.write to return 0 once (triggers OSError).
        2. First append() raises; _poisoned becomes True.
        3. Second append() raises RuntimeError immediately (no os.write call).
        4. recover() clears the poison.
        5. Third append() succeeds.
        """
        env = _make_envelope(clock=fixed_clock)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)

        real_write = os.write
        call_state = {"poison_next": True, "calls_while_poisoned": 0}

        def flaky_write(fd: int, data: bytes) -> int:
            if call_state["poison_next"]:
                call_state["poison_next"] = False
                return 0  # trigger OSError in the writer
            call_state["calls_while_poisoned"] += 1
            return real_write(fd, data)

        monkeypatch.setattr(os, "write", flaky_write)

        # 1: first append fails with OSError (os.write returned 0).
        with pytest.raises(OSError, match="returned 0"):
            await writer.append(env)
        assert writer._poisoned is True

        # 2: subsequent append raises RuntimeError immediately — os.write NOT
        # called again.
        calls_before = call_state["calls_while_poisoned"]
        with pytest.raises(RuntimeError, match="poisoned"):
            await writer.append(env)
        assert call_state["calls_while_poisoned"] == calls_before

        # 3: recover() cures the poison.
        await writer.recover()
        assert writer._poisoned is False

        # 4: subsequent append succeeds (os.write is now real).
        await writer.append(env)
        await writer.close()

        # The line should be in the file (possibly with a partial-zero-byte
        # tail trimmed by recover()).
        path = current_day_path(tmp_path, fixed_clock.now())
        recovered = list(read_log_lines(path))
        assert len(recovered) == 1
        assert recovered[0] == env

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_releases_flock(
        self,
        tmp_path: Path,
        fixed_clock: FrozenClock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """BaseException during write path triggers LOCK_UN in finally.

        Verifies the Story 11.2.3 PP3 restructuring: flock acquisition is
        inside the try block, so LOCK_UN always runs in finally — even for
        BaseException (KeyboardInterrupt, SystemExit, etc.).

        Story 11.3.1 pass-1 review P0 (Edge #7 + Blind #8): the test
        previously asserted ``LOCK_UN in flock_ops`` using set membership.
        With the tracking wrapper appending ops BEFORE calling the real
        flock, the assertion would pass even if the real ``LOCK_UN``
        syscall errored (production wraps in ``contextlib.suppress(OSError)``).
        Now: ops are appended AFTER the real flock succeeds, AND the
        assertion pins exact ordering ``[LOCK_EX, LOCK_UN]`` so a
        buggy double-release or out-of-order release is caught.
        Additionally: ``os.write`` monkeypatch is now scoped to the
        writer's own fd (P1-M Blind #7) so unrelated stdlib writes
        (logging, asyncio runner) don't crash the test.
        """
        import fcntl

        from registry_state.adapters import event_log as _elm

        if _elm._fcntl is None:  # type: ignore[attr-defined]
            pytest.skip("fcntl not available on this platform")

        env = _make_envelope(clock=fixed_clock)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)

        # Drive a no-op write so writer._fd is populated, then close so the
        # actual test path can re-open it on first append. Cleaner: pre-warm
        # by calling _ensure_current_day directly. Simpler approach: keep the
        # writer pristine, let append() open its fd, and narrow the
        # os.write monkeypatch to that fd.
        flock_ops: list[int] = []
        real_flock = fcntl.flock

        def tracking_flock(fd: int, op: int) -> None:
            # Record AFTER successful real_flock so a real-flock failure is
            # NOT recorded as success in flock_ops.
            real_flock(fd, op)
            flock_ops.append(op)

        monkeypatch.setattr(_elm._fcntl, "flock", tracking_flock)  # type: ignore[attr-defined]

        original_write = os.write

        def interrupting_write(fd: int, data: bytes) -> int:
            # P1-M Blind #7: narrow monkeypatch — only interrupt writes to
            # the writer's fd. Other os.write calls (logging, asyncio
            # internals) pass through unchanged.
            if writer._fd is not None and fd == writer._fd:
                raise KeyboardInterrupt("simulated interrupt")
            return original_write(fd, data)

        monkeypatch.setattr(os, "write", interrupting_write)

        with pytest.raises(KeyboardInterrupt):
            await writer.append(env)

        # P0 (Edge #7 + Blind #8): exact-order assertion. LOCK_EX must
        # acquire FIRST, LOCK_UN must release LAST, nothing in between,
        # and the list must NOT contain a duplicate release.
        assert flock_ops == [fcntl.LOCK_EX, fcntl.LOCK_UN], (
            f"flock ops must be [LOCK_EX, LOCK_UN] in order; got {flock_ops!r}"
        )
        assert writer._poisoned is True

    @pytest.mark.asyncio
    async def test_os_write_short_write_loops_to_completion(
        self,
        tmp_path: Path,
        fixed_clock: FrozenClock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A short write (n < len(data)) is retried until all bytes land."""
        env = _make_envelope(clock=fixed_clock)
        expected = to_canonical_json(env) + b"\n"

        real_write = os.write
        state = {"halved_once": False}

        def halving_write(fd: int, data: bytes) -> int:
            if not state["halved_once"] and len(data) > 1:
                state["halved_once"] = True
                half = len(data) // 2
                return real_write(fd, data[:half])
            return real_write(fd, data)

        monkeypatch.setattr(os, "write", halving_write)

        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        await writer.append(env)
        await writer.close()

        # Despite the short-write, the full line ends up on disk.
        path = current_day_path(tmp_path, fixed_clock.now())
        assert path.read_bytes() == expected
        recovered = list(read_log_lines(path))
        assert recovered == [env]
        assert state["halved_once"] is True


# ===========================================================================
# TestMultiDayRecovery — F5, F8 hardening
# ===========================================================================


class TestMultiDayRecovery:
    @pytest.mark.asyncio
    async def test_recover_trims_yesterday_file_too(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """recover() iterates every *.jsonl, not just today's.

        Pre-populate yesterday (with partial tail) + today (clean).  recover()
        trims both; returns the combined byte count.
        """
        today_path = tmp_path / "2026-01-01.jsonl"
        yesterday_path = tmp_path / "2025-12-31.jsonl"
        today_clean = b'{"k":"today"}\n'
        yesterday_clean = b'{"k":"yest1"}\n{"k":"yest2"}\n'
        yesterday_partial = b'{"k":"partial-15b'  # 17 bytes, no newline
        today_path.write_bytes(today_clean)
        yesterday_path.write_bytes(yesterday_clean + yesterday_partial)

        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        trimmed = await writer.recover()

        assert trimmed == len(yesterday_partial)
        assert yesterday_path.read_bytes() == yesterday_clean
        assert today_path.read_bytes() == today_clean

    @pytest.mark.asyncio
    async def test_recover_closes_held_fd(self, tmp_path: Path, fixed_clock: FrozenClock) -> None:
        """After append() opens an fd, recover() invalidates it.

        A subsequent append() must open a fresh fd (the old one was closed).
        """
        env = _make_envelope(clock=fixed_clock)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        await writer.append(env)
        assert writer._fd is not None
        held_fd = writer._fd

        await writer.recover()
        assert writer._fd is None
        assert writer._current_date is None

        # Confirm the previously-held fd is indeed closed (os.fstat raises
        # OSError with EBADF on a closed fd).
        with pytest.raises(OSError):
            os.fstat(held_fd)

        # Fresh append opens a new fd and succeeds.
        await writer.append(env)
        assert writer._fd is not None
        await writer.close()

        path = current_day_path(tmp_path, fixed_clock.now())
        recovered = list(read_log_lines(path))
        assert recovered == [env, env]


# ===========================================================================
# TestReadLogLinesEagerRaise — F7 hardening
# ===========================================================================


class TestReadLogLinesEagerRaise:
    def test_missing_file_raises_eagerly(self, tmp_path: Path) -> None:
        """read_log_lines(missing) raises BEFORE iteration, not on next()."""
        missing = tmp_path / "nonexistent.jsonl"
        with pytest.raises(FileNotFoundError):
            read_log_lines(missing)


# ===========================================================================
# TestCRLFTolerance — F7 hardening
# ===========================================================================


class TestCRLFTolerance:
    @pytest.mark.asyncio
    async def test_read_log_lines_strips_cr_from_crlf_line(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """A line with \\r\\n terminator (external-tool tampering) still parses."""
        env = _make_envelope(clock=fixed_clock)
        canonical = to_canonical_json(env)
        # Simulate a file with CRLF line endings (e.g., Windows tool / VCS
        # translation).  The writer emits LF only; any CR is external.
        path = tmp_path / "2026-01-01.jsonl"
        path.write_bytes(canonical + b"\r\n")

        recovered = list(read_log_lines(path))
        assert recovered == [env]


# ===========================================================================
# TestRolloverAtomicity — F3, F4 hardening
# ===========================================================================


class TestRolloverAtomicity:
    @pytest.mark.asyncio
    async def test_open_failure_preserves_old_fd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If os.open fails during rollover, the previous-day fd stays valid.

        1. First append opens fd for day 0.
        2. Patch os.open to raise OSError once when the new-day file is opened.
        3. Rollover attempt (clock crosses midnight) raises; _fd is unchanged.
        4. Patch restored, but we re-use day-0 directly to verify the original
           fd still works.  This is proven by checking the writer keeps the
           same _fd and can still write to the day-0 file.
        """
        day_ns = 86_400 * 1_000_000_000
        clock = TickingClock(start_now=FROZEN_EPOCH, tick_ns=day_ns)
        writer = EventLogWriter(base_dir=tmp_path, clock=clock)

        rng = Random(13)
        clk0 = FrozenClock(mono_ns=0, now=FROZEN_EPOCH)
        env1 = EventEnvelope(
            event_id=new_event_id(clock=clk0, rng=rng),
            schema_version="1.0.0",
            type="task.created",  # noqa: EVT001 test-only fixture envelope
            emitted_at=FROZEN_EPOCH,
            emitted_at_monotonic_ns=0,
            actor=_ACTOR,
            payload={"value": "day0"},
            trace_id="01917e5c-a7d1-7000-8abc-000000000000",
            request_id=new_uuid7(clock=clk0, rng=rng),
        )
        await writer.append(env1)
        fd_before = writer._fd
        date_before = writer._current_date
        assert fd_before is not None

        # Patch os.open to raise once.  The next append() advances the clock
        # to day 1, triggers rollover, which calls os.open → OSError.
        real_open = os.open
        call_state = {"raised": False}

        def flaky_open(
            path: str | bytes | os.PathLike[str],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if not call_state["raised"] and b"2026-01-02" in str(path).encode():
                call_state["raised"] = True
                raise OSError("simulated open failure")
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", flaky_open)

        clk1 = FrozenClock(mono_ns=1, now=FROZEN_EPOCH)
        env2 = EventEnvelope(
            event_id=new_event_id(clock=clk1, rng=rng),
            schema_version="1.0.0",
            type="task.created",  # noqa: EVT001 test-only fixture envelope
            emitted_at=FROZEN_EPOCH,
            emitted_at_monotonic_ns=1,
            actor=_ACTOR,
            payload={"value": "day1-fails"},
            trace_id="01917e5c-a7d1-7000-8abc-000000000000",
            request_id=new_uuid7(clock=clk1, rng=rng),
        )
        # Rollover attempts os.open → OSError bubbles up; writer is poisoned
        # (any exception poisons per F1) but _fd must NOT have been
        # corrupted — old_fd only replaces self._fd AFTER new_fd succeeds.
        with pytest.raises(OSError, match="simulated open failure"):
            await writer.append(env2)

        # Key invariant from F3/F4: self._fd still points to day-0's fd
        # (unchanged) — not None, not a dangling reference.
        assert writer._fd == fd_before
        assert writer._current_date == date_before
        assert call_state["raised"] is True

        # The old fd is still valid — prove with os.fstat.
        st = os.fstat(fd_before)
        assert st.st_size > 0

        # Cleanup: recover() clears poison + closes held fd, then close().
        await writer.recover()
        # _fd is now None after recover().
        assert writer._fd is None


# ===========================================================================
# TestFileMode — F14 hardening
# ===========================================================================


class TestFileMode:
    """Story 11.3.11 — event-log files are 0o660 (group-RW, never world-readable).

    Supersedes the F14 ``0o640`` assertion: the writer now creates day-files
    group-WRITABLE so cross-uid ``omb`` services can append/recover (the
    file-level sibling of Story 11.3.8's 0o2775 dir fix), while keeping the
    others-triad ``0`` (the non-world-readable audit invariant).
    """

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX mode bits ignored on Windows — fchmod is a no-op there",
    )
    @pytest.mark.asyncio
    async def test_created_file_is_0o660_regardless_of_umask(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """Fresh file is exactly 0o660 even under umask 022 (fchmod defeats umask).

        Pins umask to 022 — under which a bare ``os.open(..., 0o660)`` would be
        masked down to 0o640 (stripping group-write, the original bug).  The
        writer's explicit ``os.fchmod(fd, 0o660)`` (event_log.py
        ``_ensure_current_day``) must restore the group-write bit.
        """
        prev_umask = os.umask(0o022)
        try:
            env = _make_envelope(clock=fixed_clock)
            writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
            await writer.append(env)
            await writer.close()
        finally:
            os.umask(prev_umask)

        path = current_day_path(tmp_path, fixed_clock.now())
        mode_bits = path.stat().st_mode & 0o777
        assert mode_bits == 0o660, (
            f"file mode {oct(mode_bits)} != 0o660 — the explicit fchmod must "
            f"defeat umask 022 (a bare os.open would yield 0o640)"
        )

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
    @pytest.mark.asyncio
    async def test_created_file_is_never_world_accessible(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """The audit-log security invariant: others-triad is 0 (no world r/w/x)."""
        env = _make_envelope(clock=fixed_clock)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        await writer.append(env)
        await writer.close()

        path = current_day_path(tmp_path, fixed_clock.now())
        mode_bits = path.stat().st_mode & 0o777
        assert mode_bits & 0o007 == 0, (
            f"others-triad must be 0 (non-world-readable audit log); got {oct(mode_bits)}"
        )

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
    @pytest.mark.asyncio
    async def test_recovery_self_heals_pre_existing_0o640_file(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """AC2: a stale 0o640 file owned by us is chmod'd to 0o660 before r+b open.

        Simulates a file created by a pre-11.3.11 writer: mode 0o640 with a
        trailing partial line.  ``recover_all_logs`` must self-heal the mode
        (so the r+b open succeeds) AND trim the partial tail — proving the
        recovery path no longer fails on a wrong-mode same-uid file.
        """
        # Write one clean envelope, then close, then downgrade to 0o640 and
        # append a partial (un-terminated) line to force a recovery trim.
        env = _make_envelope(clock=fixed_clock)
        writer = EventLogWriter(base_dir=tmp_path, clock=fixed_clock)
        await writer.append(env)
        await writer.close()
        path = current_day_path(tmp_path, fixed_clock.now())

        os.chmod(path, 0o640)  # simulate pre-11.3.11 creation
        with open(path, "ab") as f:
            f.write(b'{"partial": true')  # no trailing newline → partial tail

        assert path.stat().st_mode & 0o777 == 0o640, "test setup: file is 0o640"

        trimmed = await recover_all_logs(tmp_path)

        assert trimmed > 0, "recovery should have trimmed the partial tail"
        # Self-healed to 0o660 (we own the file, so the AC2 chmod succeeds).
        assert path.stat().st_mode & 0o777 == 0o660, (
            f"recovery should self-heal mode to 0o660; got {oct(path.stat().st_mode & 0o777)}"
        )

    @pytest.mark.asyncio
    async def test_recovery_skips_and_continues_on_cross_uid_permission_denied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC3 (code-review L2): a PermissionError from _recover_file does NOT
        crash-loop the subscriber — recover_all_logs logs + skips + continues.

        Simulates the genuinely-cross-uid unrecoverable case (file owned by a
        DIFFERENT omb-uid, still 0o640, AC2 chmod suppressed → r+b open
        raises PermissionError) WITHOUT needing a second uid: monkeypatch
        ``_recover_file`` to raise PermissionError for one of two day-files.
        The OTHER file must still be recovered, and the call must RETURN
        (not propagate) — proving the skip-and-continue that prevents the
        Story 11.3.10-AC5 crash-loop.
        """
        from registry_state.adapters import event_log as _evt

        # Two day-files; one will "fail" recovery, the other succeeds.
        good = tmp_path / "2026-06-01.jsonl"
        bad = tmp_path / "2026-06-02.jsonl"
        good.write_bytes(b'{"ok": true}\n{"partial": ')  # partial tail → trimmable
        bad.write_bytes(b'{"x": 1}\n')

        real_recover = _evt._recover_file

        def _fake_recover(path: Path) -> int:
            if path.name == bad.name:
                raise PermissionError(13, "Permission denied", str(path))
            return real_recover(path)

        monkeypatch.setattr(_evt, "_recover_file", _fake_recover)

        # MUST NOT raise — the PermissionError on `bad` is logged + skipped.
        total = await _evt.recover_all_logs(tmp_path)

        # `good` was still recovered (its partial tail trimmed → >0 bytes),
        # proving iteration continued past the failed file.
        assert total > 0, (
            "recover_all_logs must skip the PermissionError file and STILL "
            "recover the good one (skip-and-continue, not crash) — AC3"
        )
