"""Tests for IdempotencyCacheStore (Story 2.7 AC-11).

5 test classes, 18 tests:
  TestBasicGetSet           (5) — basic get/store round-trips + durability
  TestGetOrRunConcurrency   (4) — factory-call-count serialization
  TestTTL                   (4) — configurable TTL + sweep
  TestUPSERT                (2) — race-safe double-store + re-use after sweep
  TestValidationsAndErrors  (3) — constructor validation + IdempotencyConflict

Schema-drift detection (AC-11) lives in
``services/registry-state/src/registry_state/test_idempotency_schema_drift.py``
because ``services/ → packages/`` imports are allowed; the reverse is not.

In-memory SQLite via ``sqlite+aiosqlite:///:memory:`` with StaticPool ensures
tests are fully isolated — no file I/O, no shared state between test instances.

Clock fixtures are declared inline (per Stories 2.4/2.5 convention — no new
conftest.py).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from events.clock import FROZEN_EPOCH, FrozenClock, TickingClock
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from idempotency.cache import (
    _IDEMPOTENCY_TABLE,
    CacheHit,
    IdempotencyCacheStore,
)
from idempotency.errors import IdempotencyConflict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MEM_URL = "sqlite+aiosqlite:///:memory:"
_KEY = "test-idempotency-key-0001"
_RESULT_EVT = "e-0000000000000000000000000000000001"
_REQUEST_ID = "00000000-0000-0000-0000-000000000001"


# ---------------------------------------------------------------------------
# Inline fixtures (no conftest.py per Story 2.4/2.5 convention)
# ---------------------------------------------------------------------------


def _frozen_clock(now: datetime | None = None) -> FrozenClock:
    return FrozenClock(mono_ns=0, now=now or FROZEN_EPOCH)


def _ticking_clock(tick_ns: int = 1_000_000) -> TickingClock:
    return TickingClock(tick_ns=tick_ns)


async def _make_store(
    ttl_seconds: int = 604800,
    clock: FrozenClock | TickingClock | None = None,
    url: str = _MEM_URL,
) -> tuple[IdempotencyCacheStore, async_sessionmaker[AsyncSession]]:
    """Create a store backed by an in-memory SQLite DB with the cache table."""
    engine = create_async_engine(
        url,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    async with engine.begin() as conn:
        await conn.run_sync(_IDEMPOTENCY_TABLE.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    store = IdempotencyCacheStore(
        session_maker=session_maker,
        clock=clock or _frozen_clock(),
        ttl_seconds=ttl_seconds,
    )
    return store, session_maker


# ---------------------------------------------------------------------------
# TestBasicGetSet
# ---------------------------------------------------------------------------


class TestBasicGetSet:
    """Basic get / store round-trips and durability (AC-1, AC-6)."""

    @pytest.mark.asyncio
    async def test_get_on_miss_returns_none(self) -> None:
        store, _ = await _make_store()
        result = await store.get(_KEY)
        assert result is None

    @pytest.mark.asyncio
    async def test_store_then_get_returns_cache_hit(self) -> None:
        clock = _frozen_clock()
        store, _ = await _make_store(clock=clock)
        hit = await store.store(_KEY, result_event_id=_RESULT_EVT, request_id=_REQUEST_ID)
        assert isinstance(hit, CacheHit)
        assert hit.result_event_id == _RESULT_EVT
        assert hit.request_id_on_first_hit == _REQUEST_ID
        assert hit.created_at.tzinfo is not None
        assert hit.expires_at == hit.created_at + timedelta(seconds=604800)

        # get() should return the same hit
        fetched = await store.get(_KEY)
        assert fetched == hit

    @pytest.mark.asyncio
    async def test_cross_restart_durability(self) -> None:
        """New store instance backed by same engine still finds the entry."""
        engine = create_async_engine(
            _MEM_URL,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(_IDEMPOTENCY_TABLE.metadata.create_all)
        session_maker = async_sessionmaker(engine, expire_on_commit=False)

        store1 = IdempotencyCacheStore(
            session_maker=session_maker,
            clock=_frozen_clock(),
        )
        hit = await store1.store(_KEY, result_event_id=_RESULT_EVT, request_id=_REQUEST_ID)

        # New store instance — fresh in-process cache, same DB connection
        store2 = IdempotencyCacheStore(
            session_maker=session_maker,
            clock=_frozen_clock(),
        )
        fetched = await store2.get(_KEY)
        assert fetched == hit

    @pytest.mark.asyncio
    async def test_expired_entry_returns_none(self) -> None:
        """Expired entry is deleted on access and returns None."""
        # TTL = 1 second; clock advances 2 seconds past creation
        base = FROZEN_EPOCH
        store_clock = FrozenClock(now=base)
        store, _ = await _make_store(ttl_seconds=1, clock=store_clock)
        await store.store(_KEY, result_event_id=_RESULT_EVT, request_id=_REQUEST_ID)

        # New store with clock 2 seconds in the future — entry is expired.
        # Reconstruct session_maker from the same engine so both stores see the same DB.
        engine = create_async_engine(
            _MEM_URL,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(_IDEMPOTENCY_TABLE.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)

        # Store with past clock
        past = IdempotencyCacheStore(session_maker=sm, clock=FrozenClock(now=base), ttl_seconds=1)
        await past.store(_KEY, result_event_id=_RESULT_EVT, request_id=_REQUEST_ID)

        # Read with future clock — should be expired
        future = IdempotencyCacheStore(
            session_maker=sm, clock=FrozenClock(now=base + timedelta(seconds=2)), ttl_seconds=1
        )
        result = await future.get(_KEY)
        assert result is None

    @pytest.mark.asyncio
    async def test_result_event_id_roundtrips(self) -> None:
        long_id = "e-" + "a" * 36
        store, _ = await _make_store()
        hit = await store.store(_KEY, result_event_id=long_id, request_id=_REQUEST_ID)
        assert hit.result_event_id == long_id
        fetched = await store.get(_KEY)
        assert fetched is not None
        assert fetched.result_event_id == long_id


# ---------------------------------------------------------------------------
# TestGetOrRunConcurrency
# ---------------------------------------------------------------------------


class TestGetOrRunConcurrency:
    """Factory-call serialization under concurrent load (AC-2, NFR-R4)."""

    @pytest.mark.asyncio
    async def test_single_call_factory_runs(self) -> None:
        store, _ = await _make_store()
        call_count = 0

        async def factory() -> str:
            nonlocal call_count
            call_count += 1
            return _RESULT_EVT

        hit, was_run = await store.get_or_run(_KEY, request_id=_REQUEST_ID, factory=factory)
        assert was_run is True
        assert hit.result_event_id == _RESULT_EVT
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_second_call_same_key_factory_skips(self) -> None:
        store, _ = await _make_store()
        call_count = 0

        async def factory() -> str:
            nonlocal call_count
            call_count += 1
            return _RESULT_EVT

        hit1, was_run1 = await store.get_or_run(_KEY, request_id=_REQUEST_ID, factory=factory)
        hit2, was_run2 = await store.get_or_run(_KEY, request_id=_REQUEST_ID, factory=factory)

        assert was_run1 is True
        assert was_run2 is False
        assert hit1 == hit2
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_100x_concurrent_same_key_factory_runs_once(self) -> None:
        """100 concurrent get_or_run calls for same key → factory runs exactly once.

        This is the empirical NFR-R4 proof (AC-2).
        """
        store, _ = await _make_store()
        factory_call_count: list[int] = [0]

        async def factory() -> str:
            factory_call_count[0] += 1
            # Yield to allow other coroutines to accumulate behind the lock
            await asyncio.sleep(0)
            return _RESULT_EVT

        results = await asyncio.gather(
            *[store.get_or_run(_KEY, request_id=_REQUEST_ID, factory=factory) for _ in range(100)]
        )

        hits = [r[0] for r in results]
        was_run_flags = [r[1] for r in results]

        # Factory must have run exactly once
        assert factory_call_count[0] == 1, (
            f"Factory called {factory_call_count[0]} times; expected exactly 1"
        )
        # All 100 callers got the same CacheHit
        assert all(h == hits[0] for h in hits), "Not all CacheHit values are equal"
        # Exactly one caller got was_run=True
        assert sum(1 for f in was_run_flags if f) == 1, (
            f"Expected exactly 1 was_run=True; got {sum(1 for f in was_run_flags if f)}"
        )

    @pytest.mark.asyncio
    async def test_different_keys_run_in_parallel(self) -> None:
        store, _ = await _make_store()
        order: list[str] = []

        async def factory_a() -> str:
            order.append("a_start")
            await asyncio.sleep(0)
            order.append("a_end")
            return "e-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa01"

        async def factory_b() -> str:
            order.append("b_start")
            await asyncio.sleep(0)
            order.append("b_end")
            return "e-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb01"

        hit_a_task = store.get_or_run("key-a", request_id=_REQUEST_ID, factory=factory_a)
        hit_b_task = store.get_or_run("key-b", request_id=_REQUEST_ID, factory=factory_b)
        (hit_a, _), (hit_b, _) = await asyncio.gather(hit_a_task, hit_b_task)

        assert hit_a.result_event_id == "e-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa01"
        assert hit_b.result_event_id == "e-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb01"


# ---------------------------------------------------------------------------
# TestTTL
# ---------------------------------------------------------------------------


class TestTTL:
    """TTL semantics across in-process and SQLite layers (AC-3, AC-6)."""

    @pytest.mark.asyncio
    async def test_ttl_seconds_2_expires_after_3_seconds(self) -> None:
        """Entry with ttl_seconds=2 is expired when clock advances 3 seconds."""
        base = FROZEN_EPOCH
        engine = create_async_engine(
            _MEM_URL, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        async with engine.begin() as conn:
            await conn.run_sync(_IDEMPOTENCY_TABLE.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)

        writer = IdempotencyCacheStore(session_maker=sm, clock=FrozenClock(now=base), ttl_seconds=2)
        await writer.store(_KEY, result_event_id=_RESULT_EVT, request_id=_REQUEST_ID)

        reader = IdempotencyCacheStore(
            session_maker=sm,
            clock=FrozenClock(now=base + timedelta(seconds=3)),
            ttl_seconds=2,
        )
        result = await reader.get(_KEY)
        assert result is None

    @pytest.mark.asyncio
    async def test_sweep_deletes_expired_only(self) -> None:
        """sweep_expired removes expired rows; valid rows are untouched."""
        base = FROZEN_EPOCH
        engine = create_async_engine(
            _MEM_URL, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        async with engine.begin() as conn:
            await conn.run_sync(_IDEMPOTENCY_TABLE.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)

        # Store two entries — one that will expire, one that won't
        old_store = IdempotencyCacheStore(
            session_maker=sm, clock=FrozenClock(now=base), ttl_seconds=1
        )
        await old_store.store("old-key", result_event_id=_RESULT_EVT, request_id=_REQUEST_ID)

        fresh_store = IdempotencyCacheStore(
            session_maker=sm,
            clock=FrozenClock(now=base + timedelta(seconds=2)),
            ttl_seconds=3600,
        )
        await fresh_store.store("fresh-key", result_event_id=_RESULT_EVT, request_id=_REQUEST_ID)

        # Sweep at t+2 — old-key is expired (ttl=1), fresh-key is not
        sweeper = IdempotencyCacheStore(
            session_maker=sm,
            clock=FrozenClock(now=base + timedelta(seconds=2)),
            ttl_seconds=1,
        )
        deleted = await sweeper.sweep_expired()
        assert deleted == 1

        # fresh-key survives
        result = await sweeper.get("fresh-key")
        assert result is not None

    @pytest.mark.asyncio
    async def test_sweep_at_startup_is_idempotent(self) -> None:
        """Calling sweep_expired when nothing is expired returns 0 and is safe."""
        store, _ = await _make_store(clock=_frozen_clock())
        await store.store(_KEY, result_event_id=_RESULT_EVT, request_id=_REQUEST_ID)
        deleted = await store.sweep_expired()
        assert deleted == 0
        # Entry still accessible
        assert await store.get(_KEY) is not None

    @pytest.mark.asyncio
    async def test_both_layers_honor_same_ttl_boundary(self) -> None:
        """In-process and SQLite both expire at the same clock boundary."""
        base = FROZEN_EPOCH
        engine = create_async_engine(
            _MEM_URL, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        async with engine.begin() as conn:
            await conn.run_sync(_IDEMPOTENCY_TABLE.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)

        ttl = 5
        writer = IdempotencyCacheStore(
            session_maker=sm, clock=FrozenClock(now=base), ttl_seconds=ttl
        )
        await writer.store(_KEY, result_event_id=_RESULT_EVT, request_id=_REQUEST_ID)

        # One second before the TTL boundary — still valid
        before_boundary = IdempotencyCacheStore(
            session_maker=sm,
            clock=FrozenClock(now=base + timedelta(seconds=ttl - 1)),
            ttl_seconds=ttl,
        )
        still_valid = await before_boundary.get(_KEY)
        assert still_valid is not None

        # One second past the boundary — expired
        past_boundary = IdempotencyCacheStore(
            session_maker=sm,
            clock=FrozenClock(now=base + timedelta(seconds=ttl + 1)),
            ttl_seconds=ttl,
        )
        expired = await past_boundary.get(_KEY)
        assert expired is None


# ---------------------------------------------------------------------------
# TestUPSERT
# ---------------------------------------------------------------------------


class TestUPSERT:
    """Race-safe double-store and re-use after sweep (AC-5)."""

    @pytest.mark.asyncio
    async def test_double_store_returns_first_winner(self) -> None:
        """Storing the same key twice returns the first winner's CacheHit."""
        store, _ = await _make_store()
        hit1 = await store.store(_KEY, result_event_id="e-" + "1" * 36, request_id=_REQUEST_ID)
        hit2 = await store.store(_KEY, result_event_id="e-" + "2" * 36, request_id=_REQUEST_ID)
        # Second store returns the first winner (on_conflict_do_nothing)
        assert hit1 == hit2
        assert hit2.result_event_id == "e-" + "1" * 36

    @pytest.mark.asyncio
    async def test_store_after_sweep_works(self) -> None:
        """After sweep removes an expired entry, the same key can be stored again."""
        base = FROZEN_EPOCH
        engine = create_async_engine(
            _MEM_URL, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        async with engine.begin() as conn:
            await conn.run_sync(_IDEMPOTENCY_TABLE.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)

        # Store with ttl=1
        writer = IdempotencyCacheStore(session_maker=sm, clock=FrozenClock(now=base), ttl_seconds=1)
        await writer.store(_KEY, result_event_id="e-" + "0" * 36, request_id=_REQUEST_ID)

        # Sweep at t+2 — entry is deleted
        sweeper = IdempotencyCacheStore(
            session_maker=sm,
            clock=FrozenClock(now=base + timedelta(seconds=2)),
            ttl_seconds=1,
        )
        deleted = await sweeper.sweep_expired()
        assert deleted == 1

        # Re-store the same key (TTL window has passed)
        new_hit = await sweeper.store(_KEY, result_event_id="e-" + "9" * 36, request_id=_REQUEST_ID)
        assert new_hit.result_event_id == "e-" + "9" * 36


# ---------------------------------------------------------------------------
# TestValidationsAndErrors
# ---------------------------------------------------------------------------


class TestValidationsAndErrors:
    """Constructor validation and IdempotencyConflict path (AC-7, AC-1)."""

    @pytest.mark.asyncio
    async def test_ttl_seconds_zero_raises_value_error(self) -> None:
        engine = create_async_engine(
            _MEM_URL, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        sm = async_sessionmaker(engine, expire_on_commit=False)
        with pytest.raises(ValueError, match="ttl_seconds"):
            IdempotencyCacheStore(session_maker=sm, clock=_frozen_clock(), ttl_seconds=0)

    @pytest.mark.asyncio
    async def test_max_in_process_zero_raises_value_error(self) -> None:
        engine = create_async_engine(
            _MEM_URL, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        sm = async_sessionmaker(engine, expire_on_commit=False)
        with pytest.raises(ValueError, match="max_in_process"):
            IdempotencyCacheStore(
                session_maker=sm, clock=_frozen_clock(), ttl_seconds=60, max_in_process=0
            )

    @pytest.mark.asyncio
    async def test_idempotency_conflict_raised_on_pk_collision(self) -> None:
        """IdempotencyConflict is raised when store() detects a post-INSERT miss."""
        store, _ = await _make_store()

        # Patch session.execute to simulate: INSERT succeeds (rowcount handled
        # internally by on_conflict_do_nothing), but the re-SELECT returns None
        # (row was deleted between INSERT and SELECT).
        original_session_maker = store._session_maker  # noqa: SLF001

        class _FakeResult:
            def fetchone(self) -> None:
                return None

            def fetchall(self) -> list[object]:
                return []

        class _FakeSession:
            async def execute(self, *args: object, **kwargs: object) -> _FakeResult:
                return _FakeResult()

            async def commit(self) -> None:
                pass

            async def __aenter__(self) -> _FakeSession:
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

        store._session_maker = _FakeSession  # type: ignore[assignment]  # noqa: SLF001
        with pytest.raises(IdempotencyConflict) as exc_info:
            await store.store(_KEY, result_event_id=_RESULT_EVT, request_id=_REQUEST_ID)

        assert exc_info.value.key == _KEY
        store._session_maker = original_session_maker  # noqa: SLF001
