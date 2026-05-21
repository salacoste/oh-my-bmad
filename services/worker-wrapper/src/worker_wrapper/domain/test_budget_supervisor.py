"""Unit tests for :mod:`worker_wrapper.domain.budget_supervisor` (Story 12.1 AC4).

7 tests per AC4 wording:

1. ``test_watch_returns_clean_when_cancel_event_set_first``
2. ``test_watch_fires_callback_on_matching_event``
3. ``test_watch_ignores_other_task_ids``
4. ``test_watch_ignores_non_budget_event_types``
5. ``test_watch_handles_corrupted_jsonl_line_gracefully``
6. ``test_watch_records_detection_latency``
7. ``test_watch_records_termination_latency_from_callback``

Tests use real :class:`events.envelope.EventEnvelope` instances written via
:func:`events.canonical.to_canonical_json` to a tmp JSONL file (matches the
production wire format — Epic 11 retro L6).
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from random import Random

import pytest
from events import (
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    FrozenClock,
    TickingClock,
    current_day_path,
    new_event_id,
    new_uuid7,
    to_canonical_json,
)
from events.payloads import TaskBudgetExceededPayload
from events.schema_registry import register

from worker_wrapper.domain.budget_supervisor import (
    _BudgetSupervisorResult,
    watch_for_budget_exceeded,
)

_ACTOR = Actor(kind="system", id="test")
_DEFAULT_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000000"
# Real-format task_ids: ``t-<uuid7>`` (see _TASK_ID_PATTERN in events.payloads).
_TASK_ID_PRIMARY = "t-01917e5c-a7d1-7000-8abc-000000000001"
_TASK_ID_OTHER = "t-01917e5c-a7d1-7000-8abc-0000000000aa"


@pytest.fixture(autouse=True)
def _isolated_registry() -> Generator[None, None, None]:
    """Ensure the production ``task.budget_exceeded`` payload model is registered.

    The worker-wrapper test suite shares the pytest session with packages/events;
    we re-register idempotently here so this module runs standalone too.
    """
    register("task.budget_exceeded", "1.0.0", TaskBudgetExceededPayload)
    register("task.budget_exceeded", "1.1.0", TaskBudgetExceededPayload)
    yield


def _make_budget_envelope(
    *,
    task_id: str = _TASK_ID_PRIMARY,
    tokens_used: int = 1500,
    token_limit: int = 1000,
    step: int = 5,
    mono_seed: int = 1,
) -> EventEnvelope:
    """Build a real ``task.budget_exceeded`` envelope (production wire shape)."""
    rng = Random(mono_seed)
    clk = FrozenClock(mono_ns=mono_seed, now=FROZEN_EPOCH)
    return EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.1.0",
        type="task.budget_exceeded",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskBudgetExceededPayload(
            task_id=task_id,
            tokens_used=tokens_used,
            token_limit=token_limit,
            step=step,
        ),
        trace_id=_DEFAULT_TRACE_ID,
        request_id=str(new_uuid7(clock=clk, rng=rng)),
    )


def _write_envelopes(
    event_log_dir: Path,
    envelopes: list[EventEnvelope],
    *,
    clock_now: datetime | None = None,
) -> Path:
    """Write envelopes as canonical JSONL to today's path for ``clock_now``."""
    event_log_dir.mkdir(parents=True, exist_ok=True)
    now = clock_now or FROZEN_EPOCH
    path = current_day_path(event_log_dir, now)
    with open(path, "wb") as f:
        for env in envelopes:
            f.write(to_canonical_json(env) + b"\n")
    return path


# ---------------------------------------------------------------------------
# AC4 — Test 1: clean cancel returns triggered=False, callback NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_returns_clean_when_cancel_event_set_first(tmp_path: Path) -> None:
    """Setting cancel_event before any envelope arrives → clean exit, no callback."""
    callback_calls: list[int] = []

    async def _callback() -> None:
        callback_calls.append(1)

    cancel = asyncio.Event()
    cancel.set()  # already cancelled at supervisor entry

    result = await watch_for_budget_exceeded(
        task_id=_TASK_ID_PRIMARY,
        event_log_dir=tmp_path,
        terminate_callback=_callback,
        clock=TickingClock(),
        cancel_event=cancel,
        poll_interval_s=0.05,
    )

    assert isinstance(result, _BudgetSupervisorResult)
    assert result.triggered is False
    assert result.event_id is None
    assert result.tokens_used is None
    assert result.token_limit is None
    assert callback_calls == []


# ---------------------------------------------------------------------------
# AC4 — Test 2: matching event fires callback exactly once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_fires_callback_on_matching_event(tmp_path: Path) -> None:
    """A pre-written budget event for matching task_id → triggered=True, callback fires."""
    env = _make_budget_envelope(task_id=_TASK_ID_PRIMARY, tokens_used=2000, token_limit=1000)
    _write_envelopes(tmp_path, [env])

    callback_calls: list[int] = []

    async def _callback() -> None:
        callback_calls.append(1)

    cancel = asyncio.Event()

    result = await watch_for_budget_exceeded(
        task_id=_TASK_ID_PRIMARY,
        event_log_dir=tmp_path,
        terminate_callback=_callback,
        clock=TickingClock(),
        cancel_event=cancel,
        poll_interval_s=0.05,
    )

    assert result.triggered is True
    assert result.event_id == env.event_id
    assert result.tokens_used == 2000
    assert result.token_limit == 1000
    assert result.detection_latency_s is not None
    assert result.termination_latency_s is not None
    assert callback_calls == [1]


# ---------------------------------------------------------------------------
# AC4 — Test 3: other task_ids ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_ignores_other_task_ids(tmp_path: Path) -> None:
    """A budget event for task_id=t-OTHER is ignored when watching t-1."""
    env = _make_budget_envelope(task_id=_TASK_ID_OTHER)
    _write_envelopes(tmp_path, [env])

    callback_calls: list[int] = []

    async def _callback() -> None:
        callback_calls.append(1)

    cancel = asyncio.Event()

    # Cancel after a brief poll window so the supervisor has time to scan-and-skip.
    async def _cancel_after_delay() -> None:
        await asyncio.sleep(0.15)
        cancel.set()

    cancel_task = asyncio.create_task(_cancel_after_delay())
    try:
        result = await watch_for_budget_exceeded(
            task_id=_TASK_ID_PRIMARY,
            event_log_dir=tmp_path,
            terminate_callback=_callback,
            clock=TickingClock(),
            cancel_event=cancel,
            poll_interval_s=0.05,
        )
    finally:
        await cancel_task

    assert result.triggered is False
    assert callback_calls == []


# ---------------------------------------------------------------------------
# AC4 — Test 4: non-budget event types ignored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_ignores_non_budget_event_types(tmp_path: Path) -> None:
    """A non-``task.budget_exceeded`` envelope on the same task_id is ignored.

    Implemented by hand-crafting the JSONL line with a different ``type`` —
    we can't easily construct an ``EventEnvelope`` for an arbitrary other
    event type without dragging in another payload schema. The reader
    fuzz-tolerates unknown types via pre110 back-fill skip, so this exercises
    the supervisor's ``type != "task.budget_exceeded"`` skip path.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = current_day_path(tmp_path, FROZEN_EPOCH)
    # Hand-rolled non-budget envelope: schema-registry has approval.requested
    # already from imports; emit one with our task_id.
    # We use a raw dict line so reader-skips unknown types cleanly.
    # The supervisor uses getattr(envelope, "type", "") which gracefully
    # treats unparseable lines as skipped.
    path.write_bytes(
        b'{"type":"some.other.event","payload":{"task_id":"' + _TASK_ID_PRIMARY.encode() + b'"}}\n'
    )

    callback_calls: list[int] = []

    async def _callback() -> None:
        callback_calls.append(1)

    cancel = asyncio.Event()

    async def _cancel_after_delay() -> None:
        await asyncio.sleep(0.15)
        cancel.set()

    cancel_task = asyncio.create_task(_cancel_after_delay())
    try:
        result = await watch_for_budget_exceeded(
            task_id=_TASK_ID_PRIMARY,
            event_log_dir=tmp_path,
            terminate_callback=_callback,
            clock=TickingClock(),
            cancel_event=cancel,
            poll_interval_s=0.05,
        )
    finally:
        await cancel_task

    assert result.triggered is False
    assert callback_calls == []


# ---------------------------------------------------------------------------
# AC4 — Test 5: corrupted JSONL line handled, valid event after still detected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_handles_corrupted_jsonl_line_gracefully(tmp_path: Path) -> None:
    """A malformed line followed by a valid budget envelope → still triggers."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = current_day_path(tmp_path, FROZEN_EPOCH)
    # Mix: corrupt JSON line, then a real valid envelope.
    env = _make_budget_envelope(task_id=_TASK_ID_PRIMARY, tokens_used=3000, token_limit=1000)
    canonical = to_canonical_json(env)
    path.write_bytes(b'{"oops": "no closing brace"\n' + canonical + b"\n")

    callback_calls: list[int] = []

    async def _callback() -> None:
        callback_calls.append(1)

    cancel = asyncio.Event()

    # The supervisor must tolerate the parse error on line 1 and still match
    # line 2. Provide a generous cancel timeout so multiple polls can happen.
    async def _cancel_after_delay() -> None:
        await asyncio.sleep(0.5)
        cancel.set()

    cancel_task = asyncio.create_task(_cancel_after_delay())
    try:
        result = await watch_for_budget_exceeded(
            task_id=_TASK_ID_PRIMARY,
            event_log_dir=tmp_path,
            terminate_callback=_callback,
            clock=TickingClock(),
            cancel_event=cancel,
            poll_interval_s=0.05,
        )
    finally:
        await cancel_task

    assert result.triggered is True
    assert result.tokens_used == 3000
    assert callback_calls == [1]


# ---------------------------------------------------------------------------
# AC4 — Test 6: detection latency captured via injected clock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_records_detection_latency(tmp_path: Path) -> None:
    """detection_latency_s reflects the ``Clock.monotonic_ns`` delta from start to match."""
    env = _make_budget_envelope(task_id=_TASK_ID_PRIMARY)
    _write_envelopes(tmp_path, [env])

    async def _callback() -> None:
        return None

    cancel = asyncio.Event()
    # TickingClock advances tick_ns per monotonic_ns() call; start_ns=0.
    # The supervisor calls monotonic_ns() at start, then again on each
    # match-detection success — so detection_latency_s is at least tick_ns / 1e9.
    tick_ns = 500_000_000  # 0.5s per call → detection_latency_s ≥ 0.5
    clock = TickingClock(tick_ns=tick_ns)

    result = await watch_for_budget_exceeded(
        task_id=_TASK_ID_PRIMARY,
        event_log_dir=tmp_path,
        terminate_callback=_callback,
        clock=clock,
        cancel_event=cancel,
        poll_interval_s=0.05,
    )

    assert result.triggered is True
    assert result.detection_latency_s is not None
    # First monotonic_ns() returns 0 (start_ns), second returns tick_ns →
    # delta is exactly tick_ns. Convert to seconds for the assertion.
    assert result.detection_latency_s == pytest.approx(tick_ns / 1e9, abs=1e-6)


# ---------------------------------------------------------------------------
# AC4 — Test 7: termination latency reflects callback duration (clock-injected)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_records_termination_latency_from_callback(tmp_path: Path) -> None:
    """termination_latency_s reflects the clock delta around the callback await.

    Uses a stub clock that returns explicit pre/post-callback monotonic values
    so we don't rely on real wall-clock for the assertion.
    """
    env = _make_budget_envelope(task_id=_TASK_ID_PRIMARY)
    _write_envelopes(tmp_path, [env])

    # Stub clock: returns successive monotonic_ns values from a deque.
    # Layout: start_ns=0, detection_ns=1e9 (1.0s detection),
    # term_start_ns=1.5e9, term_end_ns=4.0e9 (2.5s termination).
    monotonic_values: list[int] = [0, 1_000_000_000, 1_500_000_000, 4_000_000_000]

    class _StubClock:
        def __init__(self) -> None:
            self._idx = 0

        def now(self) -> datetime:
            return datetime(2026, 1, 1, tzinfo=UTC)

        def monotonic_ns(self) -> int:
            value = monotonic_values[self._idx]
            self._idx = min(self._idx + 1, len(monotonic_values) - 1)
            return value

    callback_calls: list[int] = []

    async def _callback() -> None:
        callback_calls.append(1)
        # Do nothing slow — termination latency is measured via the stub clock,
        # not wall-clock.

    cancel = asyncio.Event()

    result = await watch_for_budget_exceeded(
        task_id=_TASK_ID_PRIMARY,
        event_log_dir=tmp_path,
        terminate_callback=_callback,
        clock=_StubClock(),
        cancel_event=cancel,
        poll_interval_s=0.05,
    )

    assert result.triggered is True
    assert callback_calls == [1]
    # detection_latency_s = (1e9 - 0) / 1e9 = 1.0
    # termination_latency_s = (4e9 - 1.5e9) / 1e9 = 2.5
    assert result.detection_latency_s == pytest.approx(1.0, abs=1e-6)
    assert result.termination_latency_s == pytest.approx(2.5, abs=1e-6)
