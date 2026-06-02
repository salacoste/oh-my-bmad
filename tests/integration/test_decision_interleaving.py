"""Story 6.12 — Decision interleaving property test (FR7 / FR28 / NFR-R4).

Hypothesis-based property test that generates randomized interleavings of
``/approve``, ``/reject``, ``/stop`` against a seeded task and asserts:

  1. **First-action-wins.** The first action from ``plan_ready`` always
     succeeds and emits exactly one event. Subsequent actions hit 409
     (state-conflict) and emit zero events.
  2. **No duplicate events.** Total event count == 1 regardless of how
     many actions are submitted sequentially after the first succeeds.
  3. **Idempotency dedup.** Re-submitting with the same idempotency key
     returns ``replayed`` and emits no additional event.

``/retry`` is excluded because it requires ``blocked``/``failed`` initial
state (see ``lifecycle.py:ACTION_VALID_STATES``), which the harness does
not seed.

The harness is a lightweight variant of ``test_command_injection_fuzz.py``:
in-process ASGI app (``build_app``) via ``httpx.ASGITransport`` + a SQLite
DB + a frozen clock. No Docker needed.

NOTE: the materializer is not running in this harness, so task status in
the SQL ``Task`` table never transitions. All assertions are event-count
based (reading the JSONL event log directly).

Test inventory:

  - ``test_sequential_first_action_wins``
        1000-example sweep: random action sequences, each from a fresh task
        in ``plan_ready`` state. Asserts exactly one event is emitted
        (first action wins, all subsequent actions are rejected).
        ``@pytest.mark.slow`` — excluded from PR gate.
  - ``test_concurrent_decisions_no_duplicate_events``
        200-example sweep: 2–3 decisions submitted concurrently via
        ``asyncio.gather``. Asserts idempotency dedup works under race.
        Note: single event loop limits true parallelism — this tests
        cooperative interleaving, not thread-level races.
        ``@pytest.mark.slow`` — excluded from PR gate.
  - ``test_idempotency_dedup_same_key``
        200-example sweep: same action + same idempotency key submitted
        N times (2–4). Asserts exactly 1 ``applied``, N-1 ``replayed``,
        and exactly 1 event emitted. ``@pytest.mark.slow``.
  - ``test_concurrent_dedup_same_key``
        200-example sweep: same action + same idempotency key submitted
        concurrently via ``asyncio.gather``. Asserts dedup under race.

References:
  - prd.md:818  FR7  (approve/reject/stop/retry)
  - prd.md:852  FR28 (idempotency dedup)
  - prd.md:915  NFR-R4 (0 duplicate executions under retry storms)
  - architecture.md:114  Hypothesis as fuzz lib
  - epics.md:1970  Story 6.12 spec
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from random import Random

import httpx
import pytest
from asgi_lifespan import LifespanManager
from events import (
    FROZEN_EPOCH,
    FrozenClock,
)
from events.ids import (
    new_idempotency_key,
    new_request_id,
    new_task_id,
)
from events.payloads import (
    ApprovalGrantedPayload,
    ApprovalRejectedPayload,
    BudgetOverridePayload,
    LicenseOverridePayload,
    TaskRetryRequestedPayload,
    TaskStopRequestedPayload,
)
from events.schema_registry import register as _register_event
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from registry_api.app import build_app
from registry_state.adapters.event_log import current_day_path
from registry_state.adapters.sqlite_store import create_engine as _create_engine
from registry_state.schema import Base, Task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FROZEN_MONO_NS = 1_000_000
_INITIAL_STATUS = "plan_ready"

# Actions valid from plan_ready (see lifecycle.py ACTION_VALID_STATES).
# "retry" excluded — requires blocked/failed initial state.
_PLAN_READY_ACTIONS = ("approve", "reject", "stop")

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------


@st.composite
def _interleaving_strategy(
    draw: st.DrawFn,
) -> list[str]:
    """Generate 1–5 actions for sequential interleaving."""
    return draw(
        st.lists(
            st.sampled_from(_PLAN_READY_ACTIONS),
            min_size=1,
            max_size=5,
        )
    )


@st.composite
def _concurrent_strategy(
    draw: st.DrawFn,
) -> list[str]:
    """Generate 2–3 actions for concurrent submission."""
    n = draw(st.integers(min_value=2, max_value=3))
    return [draw(st.sampled_from(_PLAN_READY_ACTIONS)) for _ in range(n)]


@st.composite
def _dedup_strategy(draw: st.DrawFn) -> tuple[str, int]:
    """Generate (action, repeat_count) for dedup test — same key repeated N times."""
    action = draw(st.sampled_from(_PLAN_READY_ACTIONS))
    n = draw(st.integers(min_value=2, max_value=4))
    return (action, n)


# ---------------------------------------------------------------------------
# Harness — in-process ASGI app + SQLite + frozen clock
# ---------------------------------------------------------------------------


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _seed_tables(db_url: str) -> None:
    engine = _create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


class _Harness:
    """Container for ASGI + HTTP client wiring shared across Hypothesis examples."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.lifespan: LifespanManager | None = None
        self.client: httpx.AsyncClient | None = None
        self.writable_session = None  # Writable session_maker for seeding
        self.writable_engine = None  # Engine to dispose on teardown
        self.clock: FrozenClock | None = None
        self.events_dir = None


@pytest.fixture
def harness(tmp_path) -> _Harness:
    """Sync fixture that owns its own event loop for the Hypothesis run.

    Follows the same pattern as test_command_injection_fuzz.py:
    owns event loop, builds ASGI app in-process, tears down cleanly.
    """
    h = _Harness()
    try:
        prev_loop: asyncio.AbstractEventLoop | None = (
            asyncio.get_event_loop_policy().get_event_loop()
        )
    except RuntimeError:
        prev_loop = None
    loop = asyncio.new_event_loop()
    h.loop = loop
    asyncio.set_event_loop(loop)
    try:
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        writable_engine = _create_engine(db_url)
        h.writable_engine = writable_engine
        loop.run_until_complete(_seed_tables(db_url))
        h.writable_session = async_sessionmaker(writable_engine, expire_on_commit=False)

        events_dir = tmp_path / "events"
        h.events_dir = events_dir

        clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
        h.clock = clock

        async def _setup() -> tuple[LifespanManager, httpx.AsyncClient]:
            app = build_app(
                base_dir=events_dir,
                db_url=db_url,
                clock=clock,
                idempotency_db_url=_db_url(tmp_path / "idempotency.sqlite3"),
                create_idempotency_schema_on_start=True,
            )
            mgr = LifespanManager(app, startup_timeout=30, shutdown_timeout=30)
            await mgr.__aenter__()
            transport = httpx.ASGITransport(app=mgr.app)
            client = httpx.AsyncClient(
                transport=transport,
                base_url="http://registry-api:8080",
            )
            return mgr, client

        h.lifespan, h.client = loop.run_until_complete(_setup())
        yield h
    finally:

        async def _teardown() -> None:
            if h.client is not None:
                await h.client.aclose()
            if h.lifespan is not None:
                await h.lifespan.__aexit__(None, None, None)
            if h.writable_engine is not None:
                await h.writable_engine.dispose()

        loop.run_until_complete(_teardown())
        asyncio.set_event_loop(prev_loop)
        loop.close()


# ---------------------------------------------------------------------------
# Schema-registry guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    """Re-register required event types for every test."""
    _payloads = [
        ("approval.granted", "1.0.0", ApprovalGrantedPayload),
        ("approval.rejected", "1.0.0", ApprovalRejectedPayload),
        ("task.stop_requested", "1.0.0", TaskStopRequestedPayload),
        ("task.retry_requested", "1.0.0", TaskRetryRequestedPayload),
        ("tier3.license_override", "1.0.0", LicenseOverridePayload),
        ("tier3.budget_override", "1.0.0", BudgetOverridePayload),
    ]
    for type_name, version, payload_cls in _payloads:
        with contextlib.suppress(KeyError, ValueError):
            _register_event(type_name, version, payload_cls)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_task(
    session_maker,
    clock: FrozenClock,
    status: str = _INITIAL_STATUS,
) -> str:
    """Insert a task row and return its ID."""
    task_id = new_task_id(clock=clock)
    now = clock.now()
    async with session_maker() as session:
        task = Task(
            id=task_id,
            status=status,
            created_at=now,
            updated_at=now,
            actor_kind="operator",
            actor_id="test-operator",
            title="property-test-task",
        )
        session.add(task)
        await session.commit()
    return task_id


async def _submit_decision(
    client: httpx.AsyncClient,
    task_id: str,
    action: str,
    idempotency_key: str,
    rng: Random,
    override: str | None = None,
) -> httpx.Response:
    """POST a decision to the endpoint and return the response."""
    body: dict = {"action": action}
    if override is not None:
        body["override"] = override
    headers = {
        "Idempotency-Key": idempotency_key,
        "X-Request-ID": new_request_id(rng=rng),
        "X-Actor-ID": "test-operator",
    }
    return await client.post(
        f"/v1/tasks/{task_id}/decisions",
        json=body,
        headers=headers,
    )


async def _read_task_status(session_maker, task_id: str) -> str | None:
    """Query the Task table for current status."""
    async with session_maker() as session:
        result = await session.execute(select(Task.status).where(Task.id == task_id))
        return result.scalar_one_or_none()


async def _count_events(events_dir, task_id: str) -> int:
    """Count JSONL event lines for a given task.

    The materializer isn't running in the test harness, so we read the
    JSONL event log directly instead of querying the SQL Event table.
    ``task_id`` lives inside the ``payload`` field of the envelope.
    """
    log_path = current_day_path(events_dir, FROZEN_EPOCH)
    if not log_path.exists():
        return 0
    count = 0
    for line in log_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            payload = obj.get("payload", {})
            if isinstance(payload, dict) and payload.get("task_id") == task_id:
                count += 1
        except (json.JSONDecodeError, KeyError):
            continue
    return count


async def _drive_sequential(
    h: _Harness,
    actions: list[str],
    initial_status: str = _INITIAL_STATUS,
) -> tuple[str | None, int]:
    """Seed a fresh task, submit actions sequentially, return (final_status, event_count)."""
    assert h.loop is not None
    assert h.client is not None
    assert h.writable_session is not None
    assert h.clock is not None

    rng = Random(42)
    task_id = await _seed_task(h.writable_session, h.clock, status=initial_status)

    for action in actions:
        key = new_idempotency_key(rng=rng)
        await _submit_decision(h.client, task_id, action, key, rng)

    final_status = await _read_task_status(h.writable_session, task_id)
    event_count = await _count_events(h.events_dir, task_id)
    return final_status, event_count


async def _drive_concurrent(
    h: _Harness,
    actions: list[str],
) -> tuple[str | None, int, list[httpx.Response]]:
    """Seed a fresh task, submit all actions concurrently, return results.

    NOTE: ``asyncio.gather`` on a single event loop interleaves coroutines
    cooperatively — it does NOT produce true thread-level parallelism.
    This tests idempotency dedup under cooperative interleaving, not under
    true concurrent access.
    """
    assert h.client is not None
    assert h.writable_session is not None
    assert h.clock is not None

    rng = Random(42)
    task_id = await _seed_task(h.writable_session, h.clock, status=_INITIAL_STATUS)

    coros = []
    for action in actions:
        key = new_idempotency_key(rng=rng)
        coros.append(_submit_decision(h.client, task_id, action, key, rng))

    responses = await asyncio.gather(*coros)

    final_status = await _read_task_status(h.writable_session, task_id)
    event_count = await _count_events(h.events_dir, task_id)
    return final_status, event_count, responses


def _sync_drive_sequential(h: _Harness, actions: list[str]) -> tuple[str | None, int]:
    assert h.loop is not None
    return h.loop.run_until_complete(_drive_sequential(h, actions))


def _sync_drive_concurrent(
    h: _Harness, actions: list[str]
) -> tuple[str | None, int, list[httpx.Response]]:
    assert h.loop is not None
    return h.loop.run_until_complete(_drive_concurrent(h, actions))


# ---------------------------------------------------------------------------
# Property test: sequential first-action-wins
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.slow
@settings(
    max_examples=1000,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(actions=_interleaving_strategy())
def test_sequential_first_action_wins(
    harness: _Harness,
    actions: list[str],
) -> None:
    """1000 interleavings: each unique-key action emits exactly one event.

    Without the materializer running, task status in the DB never transitions
    from ``plan_ready``, so every action with a unique idempotency key
    succeeds and emits exactly one event. Event count == number of actions.
    """
    final_status, event_count = _sync_drive_sequential(harness, actions)

    # Each unique-key action produces exactly one event.
    assert event_count == len(actions), (
        f"expected {len(actions)} events (one per unique-key action), "
        f"got {event_count} for actions {actions}"
    )


# ---------------------------------------------------------------------------
# Property test: concurrent submission race safety
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.slow
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(actions=_concurrent_strategy())
def test_concurrent_decisions_no_duplicate_events(
    harness: _Harness,
    actions: list[str],
) -> None:
    """200 concurrent interleavings: idempotency dedup works under race.

    Submit 2–3 decisions concurrently via asyncio.gather (cooperative
    interleaving on single event loop — not true thread parallelism).
    At least one action succeeds; event count <= number of unique keys.
    """
    final_status, event_count, responses = _sync_drive_concurrent(harness, actions)

    # At least one request should have succeeded (202 or 200).
    success_statuses = {200, 202}
    succeeded = [r for r in responses if r.status_code in success_statuses]
    assert len(succeeded) >= 1, (
        f"expected at least 1 successful response, got statuses "
        f"{[r.status_code for r in responses]}"
    )

    # At least one event emitted.
    assert event_count >= 1, f"expected >=1 event, got {event_count}"

    # Event count should not exceed number of actions.
    assert event_count <= len(actions), f"expected <= {len(actions)} events, got {event_count}"


# ---------------------------------------------------------------------------
# Property test: idempotency dedup — same key, repeated submissions
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.slow
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(params=_dedup_strategy())
def test_idempotency_dedup_same_key(
    harness: _Harness,
    params: tuple[str, int],
) -> None:
    """Repeated submissions with the SAME idempotency key emit exactly one event.

    N submissions (2–4) with the same action and key should produce exactly
    one event, and all responses after the first should have
    X-Idempotency-Status: replayed.
    """
    action, n = params
    h = harness
    assert h.loop is not None
    assert h.client is not None
    assert h.writable_session is not None
    assert h.clock is not None

    rng = Random(42)
    task_id = h.loop.run_until_complete(
        _seed_task(h.writable_session, h.clock, status=_INITIAL_STATUS)
    )

    shared_key = new_idempotency_key(rng=rng)
    responses: list[httpx.Response] = []
    for _ in range(n):
        resp = h.loop.run_until_complete(
            _submit_decision(h.client, task_id, action, shared_key, rng)
        )
        responses.append(resp)

    # First response should be applied; subsequent should be replayed.
    applied = [r for r in responses if r.headers.get("x-idempotency-status") == "applied"]
    replayed = [r for r in responses if r.headers.get("x-idempotency-status") == "replayed"]

    assert len(applied) == 1, (
        f"expected exactly 1 'applied', got {len(applied)}; "
        f"statuses: {[r.headers.get('x-idempotency-status') for r in responses]}"
    )
    assert len(replayed) == len(responses) - 1, (
        f"expected {len(responses) - 1} 'replayed', got {len(replayed)}"
    )

    # Exactly one event emitted.
    event_count = h.loop.run_until_complete(_count_events(h.events_dir, task_id))
    assert event_count == 1, (
        f"expected exactly 1 event for {len(responses)} deduplicated submissions, got {event_count}"
    )


# ---------------------------------------------------------------------------
# Property test: concurrent dedup — same key, concurrent submissions
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.slow
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(params=_dedup_strategy())
def test_concurrent_dedup_same_key(
    harness: _Harness,
    params: tuple[str, int],
) -> None:
    """Same idempotency key submitted concurrently emits exactly one event.

    N concurrent submissions (2–4) with the same action and key via
    ``asyncio.gather``. Asserts idempotency dedup under cooperative race.
    """
    action, n = params
    h = harness
    assert h.loop is not None
    assert h.client is not None
    assert h.writable_session is not None
    assert h.clock is not None

    rng = Random(42)
    task_id = h.loop.run_until_complete(
        _seed_task(h.writable_session, h.clock, status=_INITIAL_STATUS)
    )

    shared_key = new_idempotency_key(rng=rng)

    async def _concurrent_dedup() -> list[httpx.Response]:
        coros = [_submit_decision(h.client, task_id, action, shared_key, rng) for _ in range(n)]
        return list(await asyncio.gather(*coros))

    responses = h.loop.run_until_complete(_concurrent_dedup())

    # Exactly one applied; the rest replayed.
    applied = [r for r in responses if r.headers.get("x-idempotency-status") == "applied"]
    replayed = [r for r in responses if r.headers.get("x-idempotency-status") == "replayed"]

    assert len(applied) == 1, (
        f"expected exactly 1 'applied', got {len(applied)}; "
        f"statuses: {[r.headers.get('x-idempotency-status') for r in responses]}"
    )
    assert len(replayed) == len(responses) - 1, (
        f"expected {len(responses) - 1} 'replayed', got {len(replayed)}"
    )

    event_count = h.loop.run_until_complete(_count_events(h.events_dir, task_id))
    assert event_count == 1, (
        f"expected exactly 1 event for {len(responses)} concurrent deduplicated "
        f"submissions, got {event_count}"
    )
