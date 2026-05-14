"""Tests for GET /v1/tasks/{task_id}/events (Story 7.5 / FR6).

Covers:
  - Envelopes returned in ascending emitted_at order
  - Filtering by ``since`` cursor
  - ``limit`` pagination
  - Empty array when no events (200, not 404)
  - Invalid task_id returns 422
  - Default limit is 100
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from httpx import ASGITransport, AsyncClient
from registry_state.adapters.sqlite_store import (  # noqa: IMP001
    create_engine,
)
from registry_state.schema import Base, Event, Task  # noqa: IMP001

from registry_api.app import build_app

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FROZEN_MONO_NS = 1_000_000
_FROZEN_CLOCK = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
_TID = "t-00000000-0000-7000-8000-000000000001"
_TID_NO_EVENTS = "t-00000000-0000-7000-8000-000000000099"


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


async def _seed_task_and_events(
    db_url: str,
    task_id: str = _TID,
    event_count: int = 5,
) -> list[str]:
    """Seed a task + N events. Returns list of event IDs."""
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        await conn.execute(
            Task.__table__.insert(),
            {
                "id": task_id,
                "status": "blocked",
                "created_at": now,
                "updated_at": now,
                "actor_kind": "operator",
                "actor_id": "test-op",
                "title": "Test task",
            },
        )

        event_ids: list[str] = []
        for i in range(event_count):
            eid = f"e-{i:012d}-0000-7000-8000-000000{i:06d}"
            event_ids.append(eid)
            # Events at 10:00, 10:01, 10:02, etc.
            emitted = datetime(2026, 1, 1, 10, i, tzinfo=UTC)
            payload = json.dumps(
                {
                    "task_id": task_id,
                    "description": f"Event {i} description",
                }
            )
            await conn.execute(
                Event.__table__.insert(),
                {
                    "id": eid,
                    "type": (
                        "task.blocker_raised" if i % 5 == 0 else "file.edited"
                    ),
                    "schema_version": "1.0.0",
                    "emitted_at": emitted,
                    "emitted_at_monotonic_ns": _FROZEN_MONO_NS + i * 1000,
                    "actor_kind": "operator",
                    "actor_id": "test-op",
                    "task_id": task_id,
                    "session_id": None,
                    "parent_event_id": None,
                    "request_id": f"req-{i:06d}",
                    "payload_json": payload,
                },
            )

        # Update task last_event_id
        await conn.execute(
            Task.__table__.update()
            .where(Task.__table__.c.id == task_id)
            .values(last_event_id=event_ids[-1])
        )

    await engine.dispose()
    return event_ids


async def _seed_task_only(db_url: str, task_id: str) -> None:
    """Seed a task with no events."""
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        await conn.execute(
            Task.__table__.insert(),
            {
                "id": task_id,
                "status": "pending",
                "created_at": now,
                "updated_at": now,
                "actor_kind": "operator",
                "actor_id": "test-op",
                "title": "Empty task",
            },
        )
    await engine.dispose()


# ---------------------------------------------------------------------------
# Client fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def events_client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    """Client with 5 seeded events."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_task_and_events(db_url, event_count=5)

    events_dir = tmp_path / "events"
    app = build_app(
        base_dir=events_dir,
        db_url=db_url,
        clock=_FROZEN_CLOCK,
    )

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app),
            base_url="http://testserver",
        ) as client,
    ):
        yield client


@pytest_asyncio.fixture
async def many_events_client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    """Client with 10 seeded events for limit test."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_task_and_events(db_url, event_count=10)

    events_dir = tmp_path / "events"
    app = build_app(
        base_dir=events_dir,
        db_url=db_url,
        clock=_FROZEN_CLOCK,
    )

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app),
            base_url="http://testserver",
        ) as client,
    ):
        yield client


@pytest_asyncio.fixture
async def no_events_client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    """Client with a task that has zero events."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_task_only(db_url, _TID_NO_EVENTS)

    events_dir = tmp_path / "events"
    app = build_app(
        base_dir=events_dir,
        db_url=db_url,
        clock=_FROZEN_CLOCK,
    )

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app),
            base_url="http://testserver",
        ) as client,
    ):
        yield client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEventsHappyPath:
    @pytest.mark.asyncio
    async def test_events_returns_envelopes_ordered_by_emitted_at(
        self, events_client: AsyncClient
    ) -> None:
        """AC #1: returns 5 envelopes in ascending emitted_at order."""
        r = await events_client.get(f"/v1/tasks/{_TID}/events")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 5

        # Ascending order
        timestamps = [e["emitted_at"] for e in body]
        assert timestamps == sorted(timestamps)

        # Envelope shape
        first = body[0]
        assert first["event_id"]
        assert first["schema_version"] == "1.0.0"
        assert first["type"] == "task.blocker_raised"
        assert "emitted_at" in first
        assert isinstance(first["emitted_at_monotonic_ns"], int)
        assert first["actor"] == {"kind": "operator", "id": "test-op"}
        assert first["session_id"] is None
        assert isinstance(first["payload"], dict)
        assert first["parent_event_id"] is None
        assert first["trace_id"] is None
        assert first["request_id"]


class TestEventsFiltering:
    @pytest.mark.asyncio
    async def test_events_filters_by_since(
        self, events_client: AsyncClient
    ) -> None:
        """AC #1: since parameter filters out older events."""
        # Events are at 10:00, 10:01, 10:02, 10:03, 10:04.
        # since=10:02 should return events at 10:02, 10:03, 10:04.
        r = await events_client.get(
            f"/v1/tasks/{_TID}/events",
            params={"since": "2026-01-01T10:02:00+00:00"},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 3
        assert body[0]["emitted_at"] >= "2026-01-01T10:02:00+00:00"


class TestEventsLimit:
    @pytest.mark.asyncio
    async def test_events_respects_limit(
        self, many_events_client: AsyncClient
    ) -> None:
        """AC #1: limit=3 returns at most 3 events."""
        r = await many_events_client.get(
            f"/v1/tasks/{_TID}/events",
            params={"limit": 3},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 3

    @pytest.mark.asyncio
    async def test_events_default_limit_is_100(
        self, events_client: AsyncClient
    ) -> None:
        """Default limit of 100 returns all events when count < 100."""
        r = await events_client.get(f"/v1/tasks/{_TID}/events")
        assert r.status_code == 200
        body = r.json()
        # 5 events seeded, all returned with default limit=100
        assert len(body) == 5


class TestEventsEmptyResult:
    @pytest.mark.asyncio
    async def test_events_returns_empty_array_when_no_events(
        self, no_events_client: AsyncClient
    ) -> None:
        """AC #1: no events → 200 with empty array, NOT 404."""
        r = await no_events_client.get(f"/v1/tasks/{_TID_NO_EVENTS}/events")
        assert r.status_code == 200
        assert r.json() == []


class TestEventsValidation:
    @pytest.mark.asyncio
    async def test_events_rejects_invalid_task_id(
        self, events_client: AsyncClient
    ) -> None:
        """Malformed task_id returns 422."""
        r = await events_client.get("/v1/tasks/invalid/events")
        assert r.status_code == 422


class TestSameTimestampOrdering:
    @pytest.mark.asyncio
    async def test_events_deterministic_order_for_same_timestamp(
        self, tmp_path: Path
    ) -> None:
        """Events with the same emitted_at are ordered by monotonic_ns."""
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)

        engine = create_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
            await conn.execute(
                Task.__table__.insert(),
                {
                    "id": _TID,
                    "status": "blocked",
                    "created_at": now,
                    "updated_at": now,
                    "actor_kind": "operator",
                    "actor_id": "test-op",
                    "title": "Test task",
                },
            )
            # Two events at the exact same emitted_at, different monotonic_ns
            for i in range(2):
                eid = f"e-00000000000{i}-0000-7000-8000-00000000000{i}"
                await conn.execute(
                    Event.__table__.insert(),
                    {
                        "id": eid,
                        "type": "file.edited",
                        "schema_version": "1.0.0",
                        "emitted_at": now,
                        "emitted_at_monotonic_ns": 1000 + i * 1000,
                        "actor_kind": "operator",
                        "actor_id": "test-op",
                        "task_id": _TID,
                        "session_id": None,
                        "parent_event_id": None,
                        "request_id": f"req-{i:06d}",
                        "payload_json": json.dumps({"index": i}),
                    },
                )
            await conn.execute(
                Task.__table__.update()
                .where(Task.__table__.c.id == _TID)
                .values(last_event_id="e-000000000001-0000-7000-8000-000000000001")
            )
        await engine.dispose()

        events_dir = tmp_path / "events"
        app = build_app(
            base_dir=events_dir,
            db_url=db_url,
            clock=_FROZEN_CLOCK,
        )

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app),
                base_url="http://testserver",
            ) as client,
        ):
            r = await client.get(f"/v1/tasks/{_TID}/events")

        assert r.status_code == 200
        body = r.json()
        assert len(body) == 2
        # Deterministic: monotonic_ns ascending
        assert body[0]["emitted_at_monotonic_ns"] < body[1]["emitted_at_monotonic_ns"]
        assert body[0]["payload"]["index"] == 0
        assert body[1]["payload"]["index"] == 1


# ---------------------------------------------------------------------------
# Story 7.5.6: Cursor pagination + trace_id documentation
# ---------------------------------------------------------------------------


class TestEventsCursorPagination:
    """AC-1: after cursor allows paginating through complete event history."""

    @pytest.mark.asyncio
    async def test_after_cursor_returns_events_after_monotonic_ns(
        self, many_events_client: AsyncClient
    ) -> None:
        """after=mono_ns_of_event_4 returns only events 5-9."""
        r = await many_events_client.get(
            f"/v1/tasks/{_TID}/events",
            params={"after": _FROZEN_MONO_NS + 4 * 1000},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 5
        # All returned events have monotonic_ns > cursor
        for ev in body:
            assert ev["emitted_at_monotonic_ns"] > _FROZEN_MONO_NS + 4 * 1000

    @pytest.mark.asyncio
    async def test_after_cursor_with_limit_paginates_correctly(
        self, many_events_client: AsyncClient
    ) -> None:
        """Two requests: first gets events 0-4, second with after cursor gets 5-9."""
        r1 = await many_events_client.get(
            f"/v1/tasks/{_TID}/events",
            params={"limit": 5},
        )
        assert r1.status_code == 200
        page1 = r1.json()
        assert len(page1) == 5

        last_mono = page1[-1]["emitted_at_monotonic_ns"]
        r2 = await many_events_client.get(
            f"/v1/tasks/{_TID}/events",
            params={"after": last_mono, "limit": 5},
        )
        assert r2.status_code == 200
        page2 = r2.json()
        assert len(page2) == 5

        # No overlap
        page1_ids = {ev["event_id"] for ev in page1}
        page2_ids = {ev["event_id"] for ev in page2}
        assert page1_ids.isdisjoint(page2_ids)

    @pytest.mark.asyncio
    async def test_after_cursor_empty_when_past_end(
        self, many_events_client: AsyncClient
    ) -> None:
        """after=mono_ns_of_last_event returns empty array."""
        # Get all events first to find the last monotonic_ns
        r_all = await many_events_client.get(
            f"/v1/tasks/{_TID}/events",
            params={"limit": 1000},
        )
        all_events = r_all.json()
        last_mono = all_events[-1]["emitted_at_monotonic_ns"]

        r = await many_events_client.get(
            f"/v1/tasks/{_TID}/events",
            params={"after": last_mono},
        )
        assert r.status_code == 200
        assert r.json() == []

    @pytest.mark.asyncio
    async def test_since_and_after_combine_correctly(
        self, many_events_client: AsyncClient
    ) -> None:
        """since + after both filter: events must match both conditions."""
        # Events at 10:00, 10:01, ..., 10:09
        # since=10:03 → events 3-9, after=mono_ns_of_5 → events 6-9
        r = await many_events_client.get(
            f"/v1/tasks/{_TID}/events",
            params={
                "since": "2026-01-01T10:03:00+00:00",
                "after": _FROZEN_MONO_NS + 5 * 1000,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 4
        for ev in body:
            assert ev["emitted_at"] >= "2026-01-01T10:03:00+00:00"
            assert ev["emitted_at_monotonic_ns"] > _FROZEN_MONO_NS + 5 * 1000


class TestTraceIdDocumentation:
    """AC-2: trace_id is None, documented as Phase 2 dependency."""

    @pytest.mark.asyncio
    async def test_trace_id_is_none_awaiting_phase_2(
        self, events_client: AsyncClient
    ) -> None:
        """trace_id remains None until Phase 2 ORM column + migration lands."""
        r = await events_client.get(f"/v1/tasks/{_TID}/events")
        assert r.status_code == 200
        body = r.json()
        for ev in body:
            assert ev["trace_id"] is None


class TestCorruptPayloadDefense:
    """Review patch: corrupt payload_json does not crash the endpoint."""

    @pytest.mark.asyncio
    async def test_corrupt_payload_json_returns_raw_fallback(
        self, tmp_path: Path
    ) -> None:
        """Invalid JSON in payload_json returns {\"_raw\": ...} instead of 500."""
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        engine = create_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
            await conn.execute(
                Task.__table__.insert(),
                {
                    "id": _TID,
                    "status": "blocked",
                    "created_at": now,
                    "updated_at": now,
                    "actor_kind": "operator",
                    "actor_id": "test-op",
                    "title": "Test task",
                },
            )
            eid = "e-000000000000-0000-7000-8000-000000000099"
            await conn.execute(
                Event.__table__.insert(),
                {
                    "id": eid,
                    "type": "file.edited",
                    "schema_version": "1.0.0",
                    "emitted_at": now,
                    "emitted_at_monotonic_ns": _FROZEN_MONO_NS,
                    "actor_kind": "operator",
                    "actor_id": "test-op",
                    "task_id": _TID,
                    "session_id": None,
                    "parent_event_id": None,
                    "request_id": "req-000099",
                    "payload_json": "{broken json!!",
                },
            )
            await conn.execute(
                Task.__table__.update()
                .where(Task.__table__.c.id == _TID)
                .values(last_event_id=eid)
            )
        await engine.dispose()

        events_dir = tmp_path / "events"
        app = build_app(
            base_dir=events_dir,
            db_url=db_url,
            clock=_FROZEN_CLOCK,
        )

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app),
                base_url="http://testserver",
            ) as client,
        ):
            r = await client.get(f"/v1/tasks/{_TID}/events")

        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["payload"] == {"_raw": "{broken json!!"}


class TestAfterParameterValidation:
    """Review patch: negative after values are rejected."""

    @pytest.mark.asyncio
    async def test_negative_after_returns_422(
        self, events_client: AsyncClient
    ) -> None:
        """after=-1 returns 422 validation error."""
        r = await events_client.get(
            f"/v1/tasks/{_TID}/events",
            params={"after": -1},
        )
        assert r.status_code == 422
