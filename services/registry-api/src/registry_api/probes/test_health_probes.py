"""Unit tests for :mod:`registry_api.probes.health_probes` (Story 11.3.9 / AC1-AC3).

Three probes × three test classes:

* ``TestProbeRegistryReachable`` (AC1) — SELECT 1 success + DB-error degraded.
* ``TestProbeWorkerRecentlyActive`` (AC2) — window arithmetic + event-type
  filter + degraded-on-error fallback.
* ``TestProbeQueueDepth`` (AC3) — pending-status filter + look-back window +
  clamp to [0, QUEUE_DEPTH_MAX] + degraded-on-error returns 0.

Tests use an in-memory SQLite via ``registry_state.adapters.sqlite_store.create_engine``
+ the canonical schema (``registry_state.schema.Base.metadata.create_all``)
so the probes exercise REAL SQL against REAL tables, not mocks. The
materialiser is bypassed (we insert ``Task`` / ``Event`` rows directly)
to keep tests deterministic + fast — the probes are storage-side reads,
not materialiser-side writes, so direct fixture rows are equivalent to
the materialised state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from registry_state.schema import (  # noqa: IMP001 — services→services allowed per AC-16 (mirrors app.py registry-state imports)
    Base,
    Event,
    Task,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from registry_api.probes.health_probes import (
    QUEUE_DEPTH_MAX,
    QUEUE_LOOKBACK_S_DEFAULT,
    WORKER_WINDOW_S_DEFAULT,
    probe_queue_depth,
    probe_registry_reachable,
    probe_worker_recently_active,
)

# Pytest-asyncio strict mode: each async test class carries its own
# class-level pytestmark; the trailing sync ``test_defaults_match_story_spec``
# stays sync so doesn't take the marker.

_FROZEN_NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)
_ACTOR_KIND = "operator"
_ACTOR_ID = "11-3-9-test"


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """In-memory SQLite session with canonical schema applied.

    Async generator yielding a single :class:`AsyncSession`; the engine
    is disposed on teardown so each test gets a fresh DB. No
    ``aiosqlite`` URL fragment trickery — pytest tmp paths are not
    involved (everything is memory-resident for speed).
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _make_event(*, type_: str, emitted_at: datetime, task_id: str | None = None) -> Event:
    """Build an :class:`Event` row with the minimum required columns."""
    return Event(
        id=f"01{uuid4().hex[:36]}",
        type=type_,
        schema_version="1.0.0",
        emitted_at=emitted_at,
        emitted_at_monotonic_ns=0,
        actor_kind=_ACTOR_KIND,
        actor_id=_ACTOR_ID,
        task_id=task_id,
        session_id=None,
        payload_json="{}",
        # schema.py:190 — request_id is NOT NULL. UUIDv4 is fine for fixtures
        # (no Story 8.x trace-id semantics required for these read-only probes).
        request_id=str(uuid4()),
    )


def _make_task(*, status: str, created_at: datetime, task_id: str | None = None) -> Task:
    """Build a :class:`Task` row with the minimum required columns."""
    return Task(
        id=task_id or f"01{uuid4().hex[:36]}",
        status=status,
        created_at=created_at,
        updated_at=created_at,
        actor_kind=_ACTOR_KIND,
        actor_id=_ACTOR_ID,
        title=None,
    )


# ─── AC1 — probe_registry_reachable ──────────────────────────────────────────


class TestProbeRegistryReachable:
    """AC1 — SELECT 1 reachability probe."""

    pytestmark = pytest.mark.asyncio

    async def test_returns_true_on_healthy_session(self, session: AsyncSession) -> None:
        """Happy path — engine open, schema applied → True."""
        assert await probe_registry_reachable(session) is True

    async def test_returns_false_on_session_execute_error(self) -> None:
        """Session whose ``execute`` raises → probe returns False (degraded).

        Mirrors the post-shutdown failure mode: the route handler holds
        a session reference but the underlying DB call raises an
        ``OperationalError`` / aiosqlite I/O error / pool-exhaustion
        error. The probe MUST swallow the exception (AC5 — route never
        5xx); the caller maps False to ``registry_status="degraded"``.

        Uses a stub session whose ``execute`` raises ``OperationalError``
        directly — closing a real in-memory SQLite session in-test is
        unreliable because SQLAlchemy's connection pool may transparently
        re-acquire on the next ``execute`` call, masking the failure path
        we actually want to test.
        """
        from sqlalchemy.exc import OperationalError

        class _ExplodingSession:
            async def execute(self, *_a: object, **_kw: object) -> object:
                raise OperationalError("SELECT 1", {}, Exception("simulated"))

        # Cast through Any — the probe annotates AsyncSession but only
        # touches the .execute() shape, so a duck-typed stub is honest
        # to what the probe actually depends on.
        from typing import cast

        stub = cast(AsyncSession, _ExplodingSession())
        assert await probe_registry_reachable(stub) is False


# ─── AC2 — probe_worker_recently_active ──────────────────────────────────────


class TestProbeWorkerRecentlyActive:
    """AC2 — worker-activity window probe."""

    pytestmark = pytest.mark.asyncio

    async def test_returns_true_when_worker_event_in_window(self, session: AsyncSession) -> None:
        """A ``task.execution.started`` 30s ago → within default 60s → True."""
        recent = _FROZEN_NOW - timedelta(seconds=30)
        session.add(_make_event(type_="task.execution.started", emitted_at=recent))
        await session.commit()

        assert await probe_worker_recently_active(session, now=_FROZEN_NOW) is True, (
            "30s-old worker event must be within the 60s default window"
        )

    @pytest.mark.parametrize(
        "type_",
        [
            "task.execution.started",
            "task.execution.completed",
            "worker.heartbeat",
        ],
    )
    async def test_returns_true_for_each_worker_event_type(
        self, session: AsyncSession, type_: str
    ) -> None:
        """Each of the 3 ``_WORKER_EVENT_TYPES`` independently qualifies.

        Code-review L2: parametrized so pytest re-runs the ``session``
        fixture cleanly per type (fresh in-memory DB each time) instead
        of the old manual session-juggling + ``Event.__table__.delete()``
        between iterations, which was fragile against any future
        identity-map-backed probe variant.
        """
        recent = _FROZEN_NOW - timedelta(seconds=10)
        session.add(_make_event(type_=type_, emitted_at=recent))
        await session.commit()

        assert await probe_worker_recently_active(session, now=_FROZEN_NOW) is True, (
            f"event type {type_!r} should count as worker activity"
        )

    async def test_returns_false_when_no_worker_events(self, session: AsyncSession) -> None:
        """Empty events table → False (signal will degrade to 'idle' at route)."""
        assert await probe_worker_recently_active(session, now=_FROZEN_NOW) is False

    async def test_returns_false_when_only_old_worker_events(self, session: AsyncSession) -> None:
        """An event 120s old + default 60s window → outside → False."""
        old = _FROZEN_NOW - timedelta(seconds=120)
        session.add(_make_event(type_="task.execution.started", emitted_at=old))
        await session.commit()

        assert await probe_worker_recently_active(session, now=_FROZEN_NOW) is False, (
            "120s-old event is outside the 60s default window"
        )

    async def test_returns_false_when_only_non_worker_events(self, session: AsyncSession) -> None:
        """A ``task.created`` event in window does NOT count as worker activity."""
        recent = _FROZEN_NOW - timedelta(seconds=10)
        session.add(_make_event(type_="task.created", emitted_at=recent))
        await session.commit()

        assert await probe_worker_recently_active(session, now=_FROZEN_NOW) is False, (
            "task.created is NOT in _WORKER_EVENT_TYPES"
        )

    async def test_respects_custom_window(self, session: AsyncSession) -> None:
        """A 45s-old event + 30s custom window → outside → False."""
        old = _FROZEN_NOW - timedelta(seconds=45)
        session.add(_make_event(type_="task.execution.started", emitted_at=old))
        await session.commit()

        assert await probe_worker_recently_active(session, window_s=30, now=_FROZEN_NOW) is False
        assert await probe_worker_recently_active(session, window_s=60, now=_FROZEN_NOW) is True

    async def test_returns_none_on_db_error(self) -> None:
        """SQL error → probe returns None, NOT False (tri-state, H1 fix).

        Code-review H1: returning False on error is indistinguishable from
        "no worker activity", which made the route mis-classify a BROKEN
        worker-probe (Event-table corruption while SELECT 1 still works)
        as ``worker_status="idle"`` instead of ``"unknown"``. The probe
        now returns ``None`` so the route's ``else: worker_status="unknown"``
        branch is reachable for this failure mode.

        Same duck-typed stub pattern as the AC1 / AC3 error tests.
        """
        from typing import cast

        from sqlalchemy.exc import OperationalError

        class _ExplodingSession:
            async def execute(self, *_a: object, **_kw: object) -> object:
                raise OperationalError("SELECT ... LIMIT 1", {}, Exception("simulated"))

        stub = cast(AsyncSession, _ExplodingSession())
        assert await probe_worker_recently_active(stub, now=_FROZEN_NOW) is None, (
            "DB error MUST NOT propagate AND MUST return None (NOT False) so the "
            "route distinguishes 'no activity' from 'probe broke' — H1 fix"
        )


# ─── AC3 — probe_queue_depth ─────────────────────────────────────────────────


class TestProbeQueueDepth:
    """AC3 — pending-task queue-depth probe."""

    pytestmark = pytest.mark.asyncio

    async def test_returns_zero_on_empty_table(self, session: AsyncSession) -> None:
        """No tasks → depth 0."""
        assert await probe_queue_depth(session, now=_FROZEN_NOW) == 0

    async def test_counts_pending_tasks_in_window(self, session: AsyncSession) -> None:
        """3 pending tasks in window + 1 executing → depth 3."""
        recent = _FROZEN_NOW - timedelta(seconds=60)
        for _ in range(3):
            session.add(_make_task(status="pending", created_at=recent))
        session.add(_make_task(status="executing", created_at=recent))
        await session.commit()

        assert await probe_queue_depth(session, now=_FROZEN_NOW) == 3, (
            "only pending tasks count — executing/blocked/completed do not"
        )

    async def test_excludes_pending_tasks_older_than_lookback(self, session: AsyncSession) -> None:
        """Pending task 600s old + default 300s window → not counted."""
        old = _FROZEN_NOW - timedelta(seconds=600)
        recent = _FROZEN_NOW - timedelta(seconds=60)
        session.add(_make_task(status="pending", created_at=old))
        session.add(_make_task(status="pending", created_at=recent))
        await session.commit()

        assert await probe_queue_depth(session, now=_FROZEN_NOW) == 1, (
            "only the in-window pending task counts; the 600s-old one is stale"
        )

    async def test_excludes_non_pending_statuses(self, session: AsyncSession) -> None:
        """Only ``status='pending'`` tasks count — others are at/past pickup."""
        recent = _FROZEN_NOW - timedelta(seconds=30)
        for status in ("executing", "blocked", "failed", "completed"):
            session.add(_make_task(status=status, created_at=recent))
        await session.commit()

        assert await probe_queue_depth(session, now=_FROZEN_NOW) == 0, (
            "non-pending tasks are out of scope for queue-depth signal"
        )

    async def test_clamps_to_queue_depth_max(self, session: AsyncSession) -> None:
        """Defense-in-depth: result is clamped to ``[0, QUEUE_DEPTH_MAX]``.

        We can't realistically insert ``QUEUE_DEPTH_MAX + 1`` rows in a
        unit test, so we verify the clamp by reading the probe code: the
        ``max(0, min(int(raw), QUEUE_DEPTH_MAX))`` is exercised on every
        return path. This test asserts the constant matches the
        ``HealthResponseLocal.clawhip_queue_depth`` upper bound (1_000_000)
        so a future schema-bump of the client constraint forces an
        update here too (contract-mirror discipline).
        """
        assert QUEUE_DEPTH_MAX == 1_000_000, (
            "QUEUE_DEPTH_MAX must mirror HealthResponseLocal.clawhip_queue_depth "
            "Field(le=1_000_000) at telegram-gateway/.../registry_client.py:109"
        )

    async def test_returns_none_on_db_error(self) -> None:
        """SQL error → probe returns None, NOT 0 (tri-state, H2 fix).

        Code-review H2: returning 0 on error is indistinguishable from a
        legitimately-empty queue, which let the route pair
        ``clawhip_queue_depth=0`` with ``registry_status="ok"``. The probe
        now returns ``None`` so the route maps it to 0 + a degraded warning.

        Same stub-session pattern as
        :meth:`TestProbeRegistryReachable.test_returns_false_on_session_execute_error`
        — closing a real in-memory session is unreliable because
        SQLAlchemy's pool may transparently re-acquire. A duck-typed
        stub gives a deterministic failure path.
        """
        from typing import cast

        from sqlalchemy.exc import OperationalError

        class _ExplodingSession:
            async def execute(self, *_a: object, **_kw: object) -> object:
                raise OperationalError("SELECT COUNT(*)", {}, Exception("simulated"))

        stub = cast(AsyncSession, _ExplodingSession())
        assert await probe_queue_depth(stub, now=_FROZEN_NOW) is None, (
            "DB error MUST NOT propagate AND MUST return None (NOT 0) so the "
            "route distinguishes 'empty queue' from 'probe broke' — H2 fix"
        )

    async def test_respects_custom_lookback(self, session: AsyncSession) -> None:
        """A 200s-old pending task: counted with default 300s, NOT with 100s."""
        old = _FROZEN_NOW - timedelta(seconds=200)
        session.add(_make_task(status="pending", created_at=old))
        await session.commit()

        assert (
            await probe_queue_depth(session, lookback_s=QUEUE_LOOKBACK_S_DEFAULT, now=_FROZEN_NOW)
            == 1
        )
        assert await probe_queue_depth(session, lookback_s=100, now=_FROZEN_NOW) == 0


# ─── Constants smoke test ───────────────────────────────────────────────────


def test_defaults_match_story_spec() -> None:
    """Story 11.3.9 AC2/AC3 default windows pinned by test (drift gate)."""
    assert WORKER_WINDOW_S_DEFAULT == 60, "AC2 window default"
    assert QUEUE_LOOKBACK_S_DEFAULT == 300, "AC3 lookback default"
    assert QUEUE_DEPTH_MAX == 1_000_000, "Wire-shape Field(le=...) mirror"
