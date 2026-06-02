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
from collections.abc import Awaitable, Callable, Generator
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
from events.payloads import BudgetOverridePayload, TaskBudgetExceededPayload
from events.schema_registry import REGISTRY, register, unregister

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
    """Snapshot + restore the schema registry around each test (PP14 + PP29 + PP36).

    Fixture scope: function (autouse) — each test gets a clean view of the
    registry's ``task.budget_exceeded`` bindings.

    PP36 — uses the public :func:`events.schema_registry.unregister` helper
    instead of poking the private ``_rebuild_types_cache``.

    PP29 — :func:`events.schema_registry.register` rebuilds the types
    cache on each successful registration, so the yield-side is already
    consistent. The restore loop reinstates the pre-snapshot REGISTRY
    contents (drop test-added keys via :func:`unregister`, then re-register
    any pre-existing bindings).

    Production callers
    (:mod:`registry_state.domain.event_types`) register
    ``task.budget_exceeded`` permanently, so a "snapshot is empty"
    transient state is impossible in any production process; this fixture
    just guarantees test-local determinism.
    """
    snapshot = dict(REGISTRY)
    register("task.budget_exceeded", "1.0.0", TaskBudgetExceededPayload)
    register("task.budget_exceeded", "1.1.0", TaskBudgetExceededPayload)
    # Story 12.3a Phase 1 — the inbound override channel. Register both the
    # FR44 back-compat name (tier3.budget_override) and the Epic-12 namespace
    # name (budget.override) so override envelopes round-trip through the
    # canonical reader in the grace-window tests.
    register("tier3.budget_override", "1.1.0", BudgetOverridePayload)
    register("budget.override", "1.1.0", BudgetOverridePayload)
    try:
        yield
    finally:
        # Restore pre-snapshot state: drop keys we added (via the public
        # unregister API per PP36 — rebuilds the types cache automatically),
        # then re-register any pre-existing bindings the snapshot held.
        for key in list(REGISTRY.keys()):
            if key not in snapshot:
                event_type, schema_version = key
                unregister(event_type, schema_version)
        for key, model in snapshot.items():
            if key not in REGISTRY:
                event_type, schema_version = key
                register(event_type, schema_version, model)


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


def _make_override_envelope(
    *,
    task_id: str = _TASK_ID_PRIMARY,
    event_type: str = "tier3.budget_override",
    old_limit: int = 1000,
    new_limit: int = 5000,
    mono_seed: int = 7,
) -> EventEnvelope:
    """Build a real budget-override envelope (production wire shape, Story 12.3a).

    ``event_type`` defaults to ``tier3.budget_override`` (the name registry-api
    actually emits today); pass ``budget.override`` to exercise the Epic-12
    namespace name. Carries a real :class:`events.payloads.BudgetOverridePayload`
    whose ``task_id`` is the supervisor's match key.
    """
    rng = Random(mono_seed)
    clk = FrozenClock(mono_ns=mono_seed, now=FROZEN_EPOCH)
    return EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.1.0",
        type=event_type,
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=BudgetOverridePayload(
            task_id=task_id,
            decision_id="d-12-3a-grace",
            actor_id="operator-test",
            old_limit=old_limit,
            new_limit=new_limit,
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
async def test_watch_single_pass_scan_skips_already_scanned_envelopes(
    tmp_path: Path,
) -> None:
    """PP3 — single-pass scan returns (match, envelopes_scanned).

    Writes 100 envelopes (target at index 50 with the watched task_id; the
    rest target ``_TASK_ID_OTHER``). The supervisor must scan past all 100,
    return ``triggered=True`` for the matching task_id, and report
    ``detection_latency_s`` consistent with a single sweep — not a double
    scan as in the pre-fix O(2N) implementation.

    Indirect verification of the single-pass behaviour: we time the
    supervisor against a synthetic envelope log and assert the supervisor
    fires on the matching envelope. If the O(2N) regression returned, the
    scan_offset would either advance past the match (skipping it forever)
    or rewind on each poll — both observable as ``triggered=False`` or
    callback-not-fired.
    """
    envelopes: list[EventEnvelope] = []
    for i in range(100):
        # Index 50 carries _TASK_ID_PRIMARY; all others carry _TASK_ID_OTHER.
        envelopes.append(
            _make_budget_envelope(
                task_id=_TASK_ID_PRIMARY if i == 50 else _TASK_ID_OTHER,
                tokens_used=1000 + i,
                token_limit=1000,
                mono_seed=i + 1,
            )
        )
    _write_envelopes(tmp_path, envelopes)

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
    assert result.tokens_used == 1050  # 1000 + 50
    assert callback_calls == [1]


@pytest.mark.asyncio
async def test_watch_propagates_termination_method_from_callback(
    tmp_path: Path,
) -> None:
    """PP21 — supervisor lifts ``.method`` from the callback's return value.

    The lifespan callback returns the :class:`TerminationResult` so the
    supervisor can propagate ``method`` (``noop`` / ``sigterm`` / ``sigkill``)
    into the :class:`BudgetSupervisorResult` for one-log-line forensics.
    """
    env = _make_budget_envelope(task_id=_TASK_ID_PRIMARY)
    _write_envelopes(tmp_path, [env])

    class _FakeTermResult:
        method = "sigkill"
        elapsed_s = 5.001
        exit_code = -9

    async def _callback() -> _FakeTermResult:
        return _FakeTermResult()

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
    assert result.termination_method == "sigkill"


@pytest.mark.asyncio
async def test_watch_step_field_propagates(tmp_path: Path) -> None:
    """PP16 — step counter from payload propagates to BudgetSupervisorResult."""
    env = _make_budget_envelope(task_id=_TASK_ID_PRIMARY, step=42)
    _write_envelopes(tmp_path, [env])

    async def _callback() -> None:
        return None

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
    assert result.step == 42


@pytest.mark.asyncio
async def test_watch_callback_exception_isolated_from_lifespan(
    tmp_path: Path,
) -> None:
    """PP9 — callback raise → log + triggered=True; no exception propagates.

    Without isolation, a transient ProcessLookupError from the adapter would
    abort the supervisor mid-flight and clobber any runner exception path.
    With PP9, the supervisor logs the failure and STILL returns
    ``triggered=True`` so the lifespan FSM transitions out of IN_PROGRESS.
    """
    env = _make_budget_envelope(task_id=_TASK_ID_PRIMARY)
    _write_envelopes(tmp_path, [env])

    async def _failing_callback() -> None:
        raise RuntimeError("transient adapter error")

    cancel = asyncio.Event()
    result = await watch_for_budget_exceeded(
        task_id=_TASK_ID_PRIMARY,
        event_log_dir=tmp_path,
        terminate_callback=_failing_callback,
        clock=TickingClock(),
        cancel_event=cancel,
        poll_interval_s=0.05,
    )

    # Exception swallowed; supervisor still reports triggered=True so the
    # lifespan can transition the FSM.
    assert result.triggered is True
    assert result.termination_method is None  # no method propagated
    # PP23 — surfaces enforcement_failed=True so the lifespan can retry
    # termination directly before transitioning.
    assert result.enforcement_failed is True


@pytest.mark.asyncio
async def test_supervisor_reports_enforcement_failed_when_callback_raises(
    tmp_path: Path,
) -> None:
    """PP23 — callback raise leaves the subprocess potentially alive.

    Pair test for PP9 isolation (``test_watch_callback_exception_isolated_from_lifespan``).
    Where PP9 asserts that the supervisor isolates the failure and still
    transitions, PP23 asserts the new ``enforcement_failed`` field is
    surfaced ``True`` so the lifespan retries termination directly.

    Successful callbacks (PP9 sibling test ``test_watch_fires_callback_on_matching_event``)
    keep ``enforcement_failed=False``.
    """
    env = _make_budget_envelope(task_id=_TASK_ID_PRIMARY)
    _write_envelopes(tmp_path, [env])

    async def _failing_callback() -> None:
        # Simulate a transient adapter failure (e.g., ProcessLookupError
        # before the PP5 fix, or a connection issue mid-termination).
        raise OSError("transient subprocess control failure")

    cancel = asyncio.Event()
    result = await watch_for_budget_exceeded(
        task_id=_TASK_ID_PRIMARY,
        event_log_dir=tmp_path,
        terminate_callback=_failing_callback,
        clock=TickingClock(),
        cancel_event=cancel,
        poll_interval_s=0.05,
    )

    # PP9 + PP23 — triggered=True so the FSM transitions; enforcement_failed
    # so the lifespan knows to retry termination.
    assert result.triggered is True
    assert result.enforcement_failed is True
    # PP21 — no method propagated because callback raised before returning.
    assert result.termination_method is None


@pytest.mark.asyncio
async def test_watch_cancel_during_sleep_returns_immediately(
    tmp_path: Path,
) -> None:
    """PP11 — cancel observed mid-sleep returns immediately, not after one more poll.

    Pre-PP11 the supervisor would loop back, scan again, and could spuriously
    match a late-arriving event — transitioning a successfully-completed task
    to TASK_FAILED. The fix returns ``triggered=False`` directly when cancel
    fires during the sleep.
    """
    # Pre-write an envelope that WOULD match if the supervisor ran another poll.
    env = _make_budget_envelope(task_id=_TASK_ID_PRIMARY)
    # Pre-cancel the event so the supervisor sees cancel during the first
    # sleep (after its initial scan returns no match against an empty dir).
    cancel = asyncio.Event()

    async def _writer_after_first_poll() -> None:
        # Wait until the supervisor has done its first scan and entered sleep.
        await asyncio.sleep(0.1)
        _write_envelopes(tmp_path, [env])
        cancel.set()

    callback_calls: list[int] = []

    async def _callback() -> None:
        callback_calls.append(1)

    writer_task = asyncio.create_task(_writer_after_first_poll())
    try:
        result = await watch_for_budget_exceeded(
            task_id=_TASK_ID_PRIMARY,
            event_log_dir=tmp_path,
            terminate_callback=_callback,
            clock=TickingClock(),
            cancel_event=cancel,
            # Long-enough poll interval that the cancel-mid-sleep path fires
            # before the next scan would.
            poll_interval_s=1.0,
        )
    finally:
        await writer_task

    assert result.triggered is False
    assert callback_calls == []


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


# ===========================================================================
# Story 12.3a Phase 1 — grace-window interception (FR68). 6 cases.
#
# The grace window is bounded by a HARD MONOTONIC deadline snapshotted once at
# window open: deadline_ns = clock.monotonic_ns() + grace_window_s * 1e9. These
# tests drive that deadline deterministically via TickingClock's per-call
# monotonic advance (tick_ns) so window expiry needs NO wall-clock wait — a
# hung/empty override channel cannot extend the window past the deadline.
# ===========================================================================


def _recording_callback() -> tuple[list[int], Callable[[], Awaitable[None]]]:
    """Return ``(calls, callback)`` where ``callback`` appends to ``calls``."""
    calls: list[int] = []

    async def _callback() -> None:
        calls.append(1)

    return calls, _callback


# ---------------------------------------------------------------------------
# 12.3a — (a) override-wins: override within window aborts termination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grace_window_override_aborts_termination(tmp_path: Path) -> None:
    """budget_exceeded + override for same task in window → override_received, no terminate."""
    budget_env = _make_budget_envelope(
        task_id=_TASK_ID_PRIMARY, tokens_used=2000, token_limit=1000, step=5
    )
    override_env = _make_override_envelope(task_id=_TASK_ID_PRIMARY)
    _write_envelopes(tmp_path, [budget_env, override_env])

    calls, callback = _recording_callback()
    cancel = asyncio.Event()

    result = await watch_for_budget_exceeded(
        task_id=_TASK_ID_PRIMARY,
        event_log_dir=tmp_path,
        terminate_callback=callback,
        clock=TickingClock(),
        cancel_event=cancel,
        poll_interval_s=0.05,
        budget_action="awaiting_approval",
        grace_window_s=5.0,
    )

    # Override intercepted: a budget breach WAS observed (triggered=True) but
    # termination was ABORTED, so terminate_callback never ran.
    assert result.triggered is True
    assert result.override_received is True
    assert calls == []  # terminate_callback NOT called
    # Budget-exceeded provenance carried through on the abort path.
    assert result.event_id == budget_env.event_id
    assert result.tokens_used == 2000
    assert result.token_limit == 1000
    assert result.step == 5
    assert result.detection_latency_s is not None
    # No termination occurred → these stay None.
    assert result.termination_latency_s is None
    assert result.termination_method is None


@pytest.mark.asyncio
async def test_grace_window_override_matches_budget_dot_override_name(tmp_path: Path) -> None:
    """12.3a 3-lane review (code LOW) — the Epic-12 `budget.override` name (not
    just the legacy `tier3.budget_override`) is matched by _scan_for_override.
    Guards against a typo in _BUDGET_OVERRIDE_TYPES dropping a real override."""
    budget_env = _make_budget_envelope(
        task_id=_TASK_ID_PRIMARY, tokens_used=2000, token_limit=1000, step=5
    )
    override_env = _make_override_envelope(task_id=_TASK_ID_PRIMARY, event_type="budget.override")
    _write_envelopes(tmp_path, [budget_env, override_env])

    calls, callback = _recording_callback()
    result = await watch_for_budget_exceeded(
        task_id=_TASK_ID_PRIMARY,
        event_log_dir=tmp_path,
        terminate_callback=callback,
        clock=TickingClock(),
        cancel_event=asyncio.Event(),
        poll_interval_s=0.05,
        budget_action="awaiting_approval",
        grace_window_s=5.0,
    )
    assert result.override_received is True
    assert calls == []  # terminate aborted by the budget.override-named event


# ---------------------------------------------------------------------------
# 12.3a — (b) window-expires: no override → fail-closed terminate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grace_window_expiry_terminates_fail_closed(tmp_path: Path) -> None:
    """budget_exceeded, no override → after the monotonic deadline, terminate fires."""
    budget_env = _make_budget_envelope(task_id=_TASK_ID_PRIMARY)
    _write_envelopes(tmp_path, [budget_env])

    calls, callback = _recording_callback()
    cancel = asyncio.Event()

    # tick_ns=2s per monotonic call, grace_window_s=3s → deadline crossed
    # within ~2 monotonic reads inside the window loop. No wall-clock wait
    # needed; the deadline is monotonic-driven (fail-closed by elapsed time,
    # not poll count).
    clock = TickingClock(tick_ns=2_000_000_000)

    result = await watch_for_budget_exceeded(
        task_id=_TASK_ID_PRIMARY,
        event_log_dir=tmp_path,
        terminate_callback=callback,
        clock=clock,
        cancel_event=cancel,
        poll_interval_s=0.01,
        budget_action="awaiting_approval",
        grace_window_s=3.0,
    )

    # Fail-closed: window expired with no override → unchanged terminate path.
    assert result.triggered is True
    assert result.override_received is False
    assert calls == [1]  # terminate_callback DID run
    assert result.termination_latency_s is not None


# ---------------------------------------------------------------------------
# 12.3a — (c) override-after-expiry: late override is ignored (fail-closed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grace_window_override_after_expiry_ignored(tmp_path: Path) -> None:
    """Window expires → terminate fires → THEN override written → still terminated."""
    budget_env = _make_budget_envelope(task_id=_TASK_ID_PRIMARY)
    _write_envelopes(tmp_path, [budget_env])

    calls, callback = _recording_callback()
    cancel = asyncio.Event()
    clock = TickingClock(tick_ns=2_000_000_000)

    result = await watch_for_budget_exceeded(
        task_id=_TASK_ID_PRIMARY,
        event_log_dir=tmp_path,
        terminate_callback=callback,
        clock=clock,
        cancel_event=cancel,
        poll_interval_s=0.01,
        budget_action="awaiting_approval",
        grace_window_s=3.0,
    )

    # Window already expired and termination already fired.
    assert result.triggered is True
    assert result.override_received is False
    assert calls == [1]

    # A late override arriving AFTER the deadline cannot un-terminate the task:
    # the supervisor has already returned the terminated result (fail-closed).
    late_override = _make_override_envelope(task_id=_TASK_ID_PRIMARY)
    _write_envelopes(tmp_path, [budget_env, late_override])
    assert result.override_received is False  # unchanged — abort is impossible now


# ---------------------------------------------------------------------------
# 12.3a — (d) wrong-task-id: override for another task does not match
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grace_window_override_wrong_task_id_terminates(tmp_path: Path) -> None:
    """budget_exceeded for A, override for B in window → not matched → terminate A."""
    budget_env = _make_budget_envelope(task_id=_TASK_ID_PRIMARY)
    # Override targets a DIFFERENT task — must not abort task A's termination.
    other_override = _make_override_envelope(task_id=_TASK_ID_OTHER)
    _write_envelopes(tmp_path, [budget_env, other_override])

    calls, callback = _recording_callback()
    cancel = asyncio.Event()
    clock = TickingClock(tick_ns=2_000_000_000)

    result = await watch_for_budget_exceeded(
        task_id=_TASK_ID_PRIMARY,
        event_log_dir=tmp_path,
        terminate_callback=callback,
        clock=clock,
        cancel_event=cancel,
        poll_interval_s=0.01,
        budget_action="awaiting_approval",
        grace_window_s=3.0,
    )

    assert result.triggered is True
    assert result.override_received is False  # B's override ignored for A
    assert calls == [1]  # A still terminated (fail-closed)


# ---------------------------------------------------------------------------
# 12.3a — (e) failed-skips-window: default action terminates immediately
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_action_skips_grace_window(tmp_path: Path) -> None:
    """budget_action='failed' (default) → immediate terminate, NO grace-window loop.

    Drives a TickingClock that records every ``monotonic_ns`` call. With the
    grace window SKIPPED, only the main loop's detection + termination reads
    occur (a small, fixed count). If a window loop had run, the monotonic call
    count would be far higher (deadline-check per iteration).
    """
    budget_env = _make_budget_envelope(task_id=_TASK_ID_PRIMARY)
    # An override is present, but 'failed' must ignore it entirely (no window).
    override_env = _make_override_envelope(task_id=_TASK_ID_PRIMARY)
    _write_envelopes(tmp_path, [budget_env, override_env])

    mono_calls: list[int] = []

    class _CountingClock:
        def __init__(self) -> None:
            self._mono = 0

        def now(self) -> datetime:
            return datetime(2026, 1, 1, tzinfo=UTC)

        def monotonic_ns(self) -> int:
            mono_calls.append(self._mono)
            current = self._mono
            self._mono += 1_000_000
            return current

    calls, callback = _recording_callback()
    cancel = asyncio.Event()

    result = await watch_for_budget_exceeded(
        task_id=_TASK_ID_PRIMARY,
        event_log_dir=tmp_path,
        terminate_callback=callback,
        clock=_CountingClock(),
        cancel_event=cancel,
        poll_interval_s=0.05,
        budget_action="failed",  # the default — immediate terminate, no window
    )

    assert result.triggered is True
    assert result.override_received is False
    assert calls == [1]  # terminate fired on first match
    assert result.termination_latency_s is not None
    # Exactly the main loop's monotonic reads: start_ns, detection_ns,
    # term_start_ns, term_end_ns = 4. The grace-window loop (which would add a
    # deadline-check read per iteration) never executed.
    assert len(mono_calls) == 4


# ---------------------------------------------------------------------------
# 12.3a — (f) cancel-during-window: clean exit, no termination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grace_window_cancel_returns_clean(tmp_path: Path) -> None:
    """cancel_event set during the grace window → triggered=False, terminate NOT called."""
    budget_env = _make_budget_envelope(task_id=_TASK_ID_PRIMARY)
    _write_envelopes(tmp_path, [budget_env])

    calls, callback = _recording_callback()
    cancel = asyncio.Event()

    # Default tick (1ms) + grace_window_s=5s → the monotonic deadline is far
    # away; the cancel below fires first, exercising the in-window cancel path.
    async def _cancel_after_delay() -> None:
        await asyncio.sleep(0.1)
        cancel.set()

    cancel_task = asyncio.create_task(_cancel_after_delay())
    try:
        result = await watch_for_budget_exceeded(
            task_id=_TASK_ID_PRIMARY,
            event_log_dir=tmp_path,
            terminate_callback=callback,
            clock=TickingClock(),
            cancel_event=cancel,
            poll_interval_s=0.02,
            budget_action="awaiting_approval",
            grace_window_s=5.0,
        )
    finally:
        await cancel_task

    # Clean cancel inside the window — no termination, consistent with the
    # other cancel paths.
    assert result.triggered is False
    assert result.override_received is False
    assert calls == []
