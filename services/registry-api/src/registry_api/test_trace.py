"""Tests for GET /v1/trace/{trace_id} (Story 9.7 / FR59a / AC8).

Story 9.7 pass-1 PH-A1 — zero test coverage on /trace route. This file
provides the minimum coverage mandated by the review:

  * test_get_trace_400_on_invalid_shape  — is_valid_trace_id rejection → 400
  * test_get_trace_200_empty_on_unknown_id — no matching rows → empty list 200
  * test_get_trace_200_ordered_results — multiple events same trace_id → ordered
  * test_get_trace_payload_roundtrip — payload_json deserialized correctly
  * test_get_trace_malformed_payload_fallback — broken payload → {"_raw": ...}
  * test_get_trace_limit_param — ?limit= caps result count + X-Trace-Truncated
  * test_get_trace_after_event_id_cursor — ?after_event_id= filters results
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
from registry_state.adapters.sqlite_store import create_engine  # noqa: IMP001
from registry_state.schema import Base, Event, Task  # noqa: IMP001

from registry_api.app import build_app

_FROZEN_MONO_NS = 1_000_000
_FROZEN_CLOCK = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)

_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000001"
_TG_TRACE_ID = "tg:99999"
_TASK_ID = "t-00000000-0000-7000-8000-000000000001"


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _create_schema(db_url: str) -> None:
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _insert_event(
    db_url: str,
    *,
    event_id: str,
    trace_id: str | None,
    mono_ns: int,
    payload_json: str | None = None,
) -> None:
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        # Ensure task exists (FK)
        await conn.execute(
            Task.__table__.insert().prefix_with("OR IGNORE"),  # type: ignore[attr-defined]
            {
                "id": _TASK_ID,
                "status": "blocked",
                "created_at": now,
                "updated_at": now,
                "actor_kind": "operator",
                "actor_id": "test-op",
                "title": "Test",
            },
        )
        await conn.execute(
            Event.__table__.insert(),  # type: ignore[attr-defined]
            {
                "id": event_id,
                "type": "task.created",
                "schema_version": "1.1.0",
                "emitted_at": now,
                "emitted_at_monotonic_ns": mono_ns,
                "actor_kind": "operator",
                "actor_id": "test-op",
                "task_id": _TASK_ID,
                "session_id": None,
                "parent_event_id": None,
                "request_id": "req-0001",
                "payload_json": payload_json or json.dumps({"key": "value"}),
                "trace_id": trace_id,
            },
        )
    await engine.dispose()


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    db_url = _db_url(tmp_path / "state.sqlite3")
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    await _create_schema(db_url)
    app = build_app(base_dir=events_dir, db_url=db_url, clock=_FROZEN_CLOCK)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
async def test_get_trace_400_on_invalid_shape(client: AsyncClient) -> None:
    """is_valid_trace_id rejects garbage → 400."""
    response = await client.get("/v1/trace/not-a-valid-trace-id")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "invalid trace_id shape" in detail


@pytest.mark.asyncio
async def test_get_trace_200_empty_on_unknown_id(client: AsyncClient) -> None:
    """No rows with this trace_id → 200 empty list."""
    response = await client.get(f"/v1/trace/{_TRACE_ID}")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_trace_200_ordered_results(tmp_path: Path) -> None:
    """Multiple events same trace_id → ordered by emitted_at_monotonic_ns asc."""
    db_url = _db_url(tmp_path / "state.sqlite3")
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    await _create_schema(db_url)

    # Insert in reverse mono_ns order to verify ordering
    _eid = "e-00000000-0000-7000-8000-000000000{:03d}"
    await _insert_event(db_url, event_id=_eid.format(2), trace_id=_TRACE_ID, mono_ns=3000)
    await _insert_event(db_url, event_id=_eid.format(1), trace_id=_TRACE_ID, mono_ns=1000)
    await _insert_event(db_url, event_id=_eid.format(3), trace_id=_TRACE_ID, mono_ns=2000)

    app = build_app(base_dir=events_dir, db_url=db_url, clock=_FROZEN_CLOCK)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get(f"/v1/trace/{_TRACE_ID}")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 3
    mono_values = [r["emitted_at_monotonic_ns"] for r in rows]
    assert mono_values == sorted(mono_values), "results must be ascending by mono_ns"


@pytest.mark.asyncio
async def test_get_trace_payload_roundtrip(tmp_path: Path) -> None:
    """payload_json is deserialized and returned as the payload field."""
    db_url = _db_url(tmp_path / "state.sqlite3")
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    await _create_schema(db_url)

    payload = {"task_id": _TASK_ID, "description": "hello world"}
    await _insert_event(
        db_url,
        event_id="e-00000000-0000-7000-8000-000000000010",
        trace_id=_TRACE_ID,
        mono_ns=1000,
        payload_json=json.dumps(payload),
    )

    app = build_app(base_dir=events_dir, db_url=db_url, clock=_FROZEN_CLOCK)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get(f"/v1/trace/{_TRACE_ID}")

    assert response.status_code == 200
    row = response.json()[0]
    assert row["trace_id"] == _TRACE_ID
    # payload is the parsed dict
    assert isinstance(row["payload"], dict)
    # extensions defaults to {}
    assert row["extensions"] == {}
    # datetime has Z suffix (canonical format)
    assert row["emitted_at"].endswith("Z")


@pytest.mark.asyncio
async def test_get_trace_malformed_payload_fallback(tmp_path: Path) -> None:
    """Malformed payload_json → {"_raw": ...} fallback (PM-B17 logged)."""
    db_url = _db_url(tmp_path / "state.sqlite3")
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    await _create_schema(db_url)

    await _insert_event(
        db_url,
        event_id="e-00000000-0000-7000-8000-000000000020",
        trace_id=_TRACE_ID,
        mono_ns=1000,
        payload_json="NOT VALID JSON{{{",
    )

    app = build_app(base_dir=events_dir, db_url=db_url, clock=_FROZEN_CLOCK)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get(f"/v1/trace/{_TRACE_ID}")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    # Falls back to _raw when JSON is broken
    assert "_raw" in rows[0]["payload"]


@pytest.mark.asyncio
async def test_get_trace_limit_param(tmp_path: Path) -> None:
    """?limit=2 caps to 2 results + X-Trace-Truncated: true header."""
    db_url = _db_url(tmp_path / "state.sqlite3")
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    await _create_schema(db_url)

    for i in range(5):
        eid = f"e-00000000-0000-7000-8000-0000000000{i:02d}"
        await _insert_event(db_url, event_id=eid, trace_id=_TRACE_ID, mono_ns=1000 + i)

    app = build_app(base_dir=events_dir, db_url=db_url, clock=_FROZEN_CLOCK)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get(f"/v1/trace/{_TRACE_ID}?limit=2")

    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.headers.get("x-trace-truncated") == "true"


@pytest.mark.asyncio
async def test_get_trace_after_event_id_cursor(tmp_path: Path) -> None:
    """?after_event_id= returns only rows with id > cursor."""
    db_url = _db_url(tmp_path / "state.sqlite3")
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    await _create_schema(db_url)

    for i in range(4):
        eid = f"e-00000000-0000-7000-8000-0000000000{i:02d}"
        await _insert_event(db_url, event_id=eid, trace_id=_TRACE_ID, mono_ns=1000 + i)

    # Get row IDs to find a cursor
    engine = create_engine(db_url)
    from registry_state.schema import Event as _Event  # noqa: PLC0415, IMP001
    from sqlalchemy import select  # noqa: PLC0415

    async with engine.connect() as conn:
        result = await conn.execute(
            select(_Event.id)
            .where(_Event.trace_id == _TRACE_ID)
            .order_by(_Event.emitted_at_monotonic_ns)
        )
        row_ids = [r[0] for r in result.fetchall()]
    await engine.dispose()

    pivot_id = row_ids[1]  # after 2nd row → expect 2 results

    app = build_app(base_dir=events_dir, db_url=db_url, clock=_FROZEN_CLOCK)
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get(f"/v1/trace/{_TRACE_ID}?after_event_id={pivot_id}")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2
    for row in rows:
        assert row["event_id"] not in row_ids[:2], "cursor should exclude first 2 rows"
