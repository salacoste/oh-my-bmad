"""Route-level tests for GET /v1/events/replay + GET /v1/tasks/{task_id}/history

(Phase 12 / Stories 61-1 and 61-2).

Uses the full ``build_app`` + ``LifespanManager`` pattern so the replay
engine reads real JSONL files. Tests write JSONL fixtures into ``tmp_path``
and verify both the replay and history endpoints.

Schema registration: ``task.created`` is registered via autouse fixture to
ensure replay's materializer can handle the event type.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from random import Random

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock, TaskCreatedPayload
from events.canonical import to_canonical_json
from events.envelope import Actor, EventEnvelope
from events.ids import new_event_id, new_request_id, new_task_id, new_uuid7
from events.schema_registry import register as _reg
from httpx import ASGITransport, AsyncClient
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — services→services allowed per AC-16
    create_engine as _create_engine,
)
from registry_state.schema import (  # noqa: IMP001 — services→services allowed per AC-16
    Base,
)

from registry_api.app import build_app

_FROZEN_MONO_NS = 1_000_000
_RNG = Random(42)
_CLOCK = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
_TASK_ID = new_task_id(clock=_CLOCK, rng=_RNG)
_EVENT_ID = new_event_id(clock=_CLOCK, rng=_RNG)


@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    """Re-register task.created before each test."""
    _reg("task.created", "1.0.0", TaskCreatedPayload)


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _seed_tables(db_url: str) -> None:
    """Create all ORM tables in the writable DB at *db_url*."""
    engine = _create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


def _make_task_created_envelope(
    *,
    task_id: str = _TASK_ID,
    event_id: str = _EVENT_ID,
    title: str = "test task",
    mono_ns: int = _FROZEN_MONO_NS,
    emitted_at: datetime = FROZEN_EPOCH,
) -> EventEnvelope:
    """Create a task.created envelope for test fixtures."""
    clock = FrozenClock(mono_ns=mono_ns, now=emitted_at)
    return EventEnvelope.create(
        event_id=event_id,
        type="task.created",
        schema_version="1.0.0",
        emitted_at=emitted_at,
        emitted_at_monotonic_ns=mono_ns,
        actor=Actor(kind="operator", id="test-op"),
        payload=TaskCreatedPayload(task_id=task_id, title=title),
        request_id=new_request_id(clock=clock, rng=Random(99)),
        trace_id=new_uuid7(clock=clock),
        parent_event_id=None,
    )


def _write_jsonl(
    events_dir: Path,
    date: str,
    envelopes: list[EventEnvelope],
) -> None:
    """Write envelopes as JSONL lines to ``events_dir/{date}.jsonl``."""
    events_dir.mkdir(parents=True, exist_ok=True)
    path = events_dir / f"{date}.jsonl"
    with open(path, "wb") as f:
        for env in envelopes:
            f.write(to_canonical_json(env) + b"\n")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fixed_clock() -> FrozenClock:
    """Stationary clock at FROZEN_EPOCH with mono_ns=1_000_000."""
    return FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)


@pytest_asyncio.fixture(loop_scope="function")
async def client_with_events(
    tmp_path: Path, fixed_clock: FrozenClock
) -> AsyncGenerator[AsyncClient, None]:
    """Client with a seeded DB and JSONL event log containing one task.created."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables(db_url)

    events_dir = tmp_path / "events"
    _write_jsonl(
        events_dir,
        "2026-06-09",
        [
            _make_task_created_envelope(
                task_id=_TASK_ID,
                event_id=_EVENT_ID,
                title="test task",
                mono_ns=5000,
                emitted_at=datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC),
            ),
        ],
    )

    app = build_app(base_dir=events_dir, db_url=db_url, clock=fixed_clock)
    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client


@pytest_asyncio.fixture(loop_scope="function")
async def client_empty_log(
    tmp_path: Path, fixed_clock: FrozenClock
) -> AsyncGenerator[AsyncClient, None]:
    """Client with empty JSONL event log."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables(db_url)

    events_dir = tmp_path / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    app = build_app(base_dir=events_dir, db_url=db_url, clock=fixed_clock)
    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client


@pytest_asyncio.fixture(loop_scope="function")
async def client_with_paginated_events(
    tmp_path: Path, fixed_clock: FrozenClock
) -> AsyncGenerator[AsyncClient, None]:
    """Client with multiple events for the same task (pagination tests)."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables(db_url)

    events_dir = tmp_path / "events"
    envelopes = []
    for i in range(5):
        clock_i = FrozenClock(
            mono_ns=1000 + i * 1000,
            now=datetime(2026, 6, 9, 12, i, 0, tzinfo=UTC),
        )
        envelopes.append(
            _make_task_created_envelope(
                task_id=_TASK_ID,
                event_id=new_event_id(clock=clock_i, rng=Random(i)),
                title=f"step-{i}",
                mono_ns=1000 + i * 1000,
                emitted_at=datetime(2026, 6, 9, 12, i, 0, tzinfo=UTC),
            )
        )
    _write_jsonl(events_dir, "2026-06-09", envelopes)

    app = build_app(base_dir=events_dir, db_url=db_url, clock=fixed_clock)
    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client


# ---------------------------------------------------------------------------
# Story 61-1: GET /v1/events/replay
# ---------------------------------------------------------------------------


class TestGetReplay:
    """Tests for GET /v1/events/replay (Story 61-1)."""

    @pytest.mark.asyncio
    async def test_replay_by_sequence_returns_200(self, client_with_events: AsyncClient) -> None:
        """GET /v1/events/replay?to_sequence=5000 returns 200 with state."""
        resp = await client_with_events.get("/v1/events/replay?to_sequence=5000")
        assert resp.status_code == 200, f"body={resp.text!r}"
        body = resp.json()
        assert "state" in body
        assert "event_count" in body
        assert body["event_count"] >= 1
        assert body["sequence_start"] <= body["sequence_end"]

    @pytest.mark.asyncio
    async def test_replay_by_timestamp_returns_200(self, client_with_events: AsyncClient) -> None:
        """GET /v1/events/replay?to_timestamp=2026-06-09T12:00:00Z returns 200."""
        resp = await client_with_events.get(
            "/v1/events/replay?to_timestamp=2026-06-09T12:00:00%2B00:00"
        )
        assert resp.status_code == 200, f"body={resp.text!r}"
        body = resp.json()
        assert body["event_count"] >= 1

    @pytest.mark.asyncio
    async def test_replay_both_params_returns_400(self, client_with_events: AsyncClient) -> None:
        """Both to_timestamp and to_sequence -> 400."""
        resp = await client_with_events.get(
            "/v1/events/replay?to_sequence=5000&to_timestamp=2026-06-09T12:00:00Z"
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_replay_neither_param_returns_400(self, client_with_events: AsyncClient) -> None:
        """No target parameter -> 400."""
        resp = await client_with_events.get("/v1/events/replay")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_replay_invalid_timestamp_returns_400(
        self, client_with_events: AsyncClient
    ) -> None:
        """Malformed ISO 8601 timestamp -> 400."""
        resp = await client_with_events.get("/v1/events/replay?to_timestamp=not-a-timestamp")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_replay_empty_log_returns_200_with_zero_count(
        self, client_empty_log: AsyncClient
    ) -> None:
        """Empty event log returns 200 with event_count=0."""
        resp = await client_empty_log.get("/v1/events/replay?to_sequence=9999")
        assert resp.status_code == 200
        body = resp.json()
        assert body["event_count"] == 0


# ---------------------------------------------------------------------------
# Story 61-2: GET /v1/tasks/{task_id}/history
# ---------------------------------------------------------------------------


class TestGetTaskHistory:
    """Tests for GET /v1/tasks/{task_id}/history (Story 61-2)."""

    @pytest.mark.asyncio
    async def test_history_returns_200_with_events(self, client_with_events: AsyncClient) -> None:
        """GET /v1/tasks/{task_id}/history returns 200 with events."""
        resp = await client_with_events.get(f"/v1/tasks/{_TASK_ID}/history")
        assert resp.status_code == 200, f"body={resp.text!r}"
        body = resp.json()
        assert "events" in body
        assert body["total"] >= 1
        assert body["limit"] == 100
        assert body["offset"] == 0
        # Validate entry shape
        entry = body["events"][0]
        assert "sequence_number" in entry
        assert "emitted_at" in entry
        assert "event_type" in entry
        assert "actor_kind" in entry
        assert "actor_id" in entry
        assert "trace_id" in entry
        assert "payload_summary" in entry

    @pytest.mark.asyncio
    async def test_history_nonexistent_task_returns_404(
        self, client_with_events: AsyncClient
    ) -> None:
        """GET /v1/tasks/nonexistent/history -> 404."""
        fake_id = "t-00000000-0000-7000-8000-000000000000"
        resp = await client_with_events.get(f"/v1/tasks/{fake_id}/history")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_history_pagination_works(
        self, client_with_paginated_events: AsyncClient
    ) -> None:
        """Pagination with limit + offset returns the correct page."""
        # First page: limit=2, offset=0
        resp = await client_with_paginated_events.get(
            f"/v1/tasks/{_TASK_ID}/history?limit=2&offset=0"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        assert len(body["events"]) == 2
        assert body["limit"] == 2
        assert body["offset"] == 0

        # Second page: limit=2, offset=2
        resp2 = await client_with_paginated_events.get(
            f"/v1/tasks/{_TASK_ID}/history?limit=2&offset=2"
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["total"] == 5
        assert len(body2["events"]) == 2
        # Verify ordering: second page's first entry has higher sequence
        assert body2["events"][0]["sequence_number"] > body["events"][0]["sequence_number"]

    @pytest.mark.asyncio
    async def test_history_events_ordered_by_sequence(
        self, client_with_paginated_events: AsyncClient
    ) -> None:
        """Events are ordered by sequence_number ascending."""
        resp = await client_with_paginated_events.get(f"/v1/tasks/{_TASK_ID}/history")
        assert resp.status_code == 200
        body = resp.json()
        seq_numbers = [e["sequence_number"] for e in body["events"]]
        assert seq_numbers == sorted(seq_numbers)

    @pytest.mark.asyncio
    async def test_history_default_limit_is_100(
        self, client_with_paginated_events: AsyncClient
    ) -> None:
        """Default limit parameter is 100."""
        resp = await client_with_paginated_events.get(f"/v1/tasks/{_TASK_ID}/history")
        assert resp.status_code == 200
        assert resp.json()["limit"] == 100

    @pytest.mark.asyncio
    async def test_history_invalid_task_id_returns_422(
        self, client_with_events: AsyncClient
    ) -> None:
        """Malformed task_id -> 422 validation error."""
        resp = await client_with_events.get("/v1/tasks/not-a-uuid/history")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Story 62-1: GET /v1/events/replay/validate
# ---------------------------------------------------------------------------


async def _insert_task_row(
    db_url: str,
    task_id: str,
    title: str,
    created_at: datetime,
    updated_at: datetime,
) -> None:
    """Insert a task row directly into the live DB for validate tests."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(db_url)
    session_maker = async_sessionmaker(engine)
    async with session_maker() as session:
        from registry_state.schema import (  # noqa: IMP001 — route tests seed registry-state ORM rows for replay validation
            Task as TaskRow,
        )

        task = TaskRow(
            id=task_id,
            status="pending",
            title=title,
            actor_kind="operator",
            actor_id="test-op",
            created_at=created_at,
            updated_at=updated_at,
        )
        session.add(task)
        await session.commit()
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")
async def client_validate_matching(
    tmp_path: Path, fixed_clock: FrozenClock
) -> AsyncGenerator[AsyncClient, None]:
    """Client where live DB and replayed state both contain the same task."""
    db_path = tmp_path / "state.sqlite3"
    db_url_str = _db_url(db_path)
    await _seed_tables(db_url_str)

    # Write JSONL event
    events_dir = tmp_path / "events"
    _write_jsonl(
        events_dir,
        "2026-06-09",
        [
            _make_task_created_envelope(
                task_id=_TASK_ID,
                event_id=_EVENT_ID,
                title="matching task",
                mono_ns=5000,
                emitted_at=datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC),
            ),
        ],
    )

    # Insert matching row into live DB
    _emitted_at = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
    await _insert_task_row(db_url_str, _TASK_ID, "matching task", _emitted_at, _emitted_at)

    app = build_app(base_dir=events_dir, db_url=db_url_str, clock=fixed_clock)
    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client


@pytest_asyncio.fixture(loop_scope="function")
async def client_validate_mismatch(
    tmp_path: Path, fixed_clock: FrozenClock
) -> AsyncGenerator[AsyncClient, None]:
    """Client where live DB has a different title than the replayed state."""
    db_path = tmp_path / "state.sqlite3"
    db_url_str = _db_url(db_path)
    await _seed_tables(db_url_str)

    events_dir = tmp_path / "events"
    _write_jsonl(
        events_dir,
        "2026-06-09",
        [
            _make_task_created_envelope(
                task_id=_TASK_ID,
                event_id=_EVENT_ID,
                title="replayed title",
                mono_ns=5000,
                emitted_at=datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC),
            ),
        ],
    )

    # Insert row with DIFFERENT title into live DB
    _emitted_at = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
    await _insert_task_row(db_url_str, _TASK_ID, "live title", _emitted_at, _emitted_at)

    app = build_app(base_dir=events_dir, db_url=db_url_str, clock=fixed_clock)
    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client


class TestValidateReplay:
    """Tests for GET /v1/events/replay/validate (Story 62-1)."""

    @pytest.mark.asyncio
    async def test_validate_matching_state_returns_empty_diffs(
        self, client_validate_matching: AsyncClient
    ) -> None:
        """When replayed and live state match, diffs list is empty."""
        resp = await client_validate_matching.get("/v1/events/replay/validate")
        assert resp.status_code == 200, f"body={resp.text!r}"
        body = resp.json()
        assert body["diffs"] == []
        assert body["mismatching_fields"] == 0

    @pytest.mark.asyncio
    async def test_validate_mismatching_state_reports_diff(
        self, client_validate_mismatch: AsyncClient
    ) -> None:
        """When a field differs, it appears in diffs with expected vs actual."""
        resp = await client_validate_mismatch.get("/v1/events/replay/validate")
        assert resp.status_code == 200, f"body={resp.text!r}"
        body = resp.json()
        assert body["mismatching_fields"] >= 1
        title_diffs = [d for d in body["diffs"] if d["field"] == "title"]
        assert len(title_diffs) >= 1
        diff = title_diffs[0]
        assert diff["expected"] == "replayed title"
        assert diff["actual"] == "live title"

    @pytest.mark.asyncio
    async def test_validate_response_includes_counts(
        self, client_validate_matching: AsyncClient
    ) -> None:
        """Response includes total_fields, matching_fields, mismatching_fields."""
        resp = await client_validate_matching.get("/v1/events/replay/validate")
        assert resp.status_code == 200, f"body={resp.text!r}"
        body = resp.json()
        assert "total_fields" in body
        assert "matching_fields" in body
        assert "mismatching_fields" in body
        assert body["total_fields"] == body["matching_fields"] + body["mismatching_fields"]


# ---------------------------------------------------------------------------
# Story 62-2: POST + GET /v1/events/replay/snapshots
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="function")
async def client_for_snapshots(
    tmp_path: Path, fixed_clock: FrozenClock
) -> AsyncGenerator[AsyncClient, None]:
    """Client with a seeded DB and JSONL event log, REPLAY_SNAPSHOT_DIR set."""
    import os

    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables(db_url)

    events_dir = tmp_path / "events"
    _write_jsonl(
        events_dir,
        "2026-06-09",
        [
            _make_task_created_envelope(
                task_id=_TASK_ID,
                event_id=_EVENT_ID,
                title="snapshot task",
                mono_ns=5000,
                emitted_at=datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC),
            ),
        ],
    )

    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)

    # Set REPLAY_SNAPSHOT_DIR so the route picks it up
    prev = os.environ.get("REPLAY_SNAPSHOT_DIR")
    os.environ["REPLAY_SNAPSHOT_DIR"] = str(snap_dir)
    try:
        app = build_app(base_dir=events_dir, db_url=db_url, clock=fixed_clock)
        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            yield client
    finally:
        if prev is None:
            os.environ.pop("REPLAY_SNAPSHOT_DIR", None)
        else:
            os.environ["REPLAY_SNAPSHOT_DIR"] = prev


class TestCreateSnapshotEndpoint:
    """Tests for POST /v1/events/replay/snapshots (Story 62-2)."""

    @pytest.mark.asyncio
    async def test_create_returns_201(self, client_for_snapshots: AsyncClient) -> None:
        """POST /v1/events/replay/snapshots returns 201."""
        resp = await client_for_snapshots.post("/v1/events/replay/snapshots")
        assert resp.status_code == 201, f"body={resp.text!r}"
        body = resp.json()
        assert "snapshot_id" in body
        assert "sequence_number" in body
        assert "timestamp" in body
        assert "size_bytes" in body
        assert body["sequence_number"] == 5000

    @pytest.mark.asyncio
    async def test_create_produces_file(
        self, client_for_snapshots: AsyncClient, tmp_path: Path
    ) -> None:
        """POST creates a snapshot file in REPLAY_SNAPSHOT_DIR."""
        resp = await client_for_snapshots.post("/v1/events/replay/snapshots")
        assert resp.status_code == 201
        snap_id = resp.json()["snapshot_id"]

        snap_dir = tmp_path / "snapshots"
        snap_file = snap_dir / f"{snap_id}.json"
        assert snap_file.is_file()


class TestListSnapshotsEndpoint:
    """Tests for GET /v1/events/replay/snapshots (Story 62-2)."""

    @pytest.mark.asyncio
    async def test_list_empty_returns_200(self, client_for_snapshots: AsyncClient) -> None:
        """GET returns 200 with empty list when no snapshots exist."""
        resp = await client_for_snapshots.get("/v1/events/replay/snapshots")
        assert resp.status_code == 200, f"body={resp.text!r}"
        body = resp.json()
        assert body["snapshots"] == []
        assert body["total"] == 0

    @pytest.mark.asyncio
    async def test_list_after_create_returns_snapshot(
        self, client_for_snapshots: AsyncClient
    ) -> None:
        """GET returns the snapshot created by POST."""
        # Create one
        post_resp = await client_for_snapshots.post("/v1/events/replay/snapshots")
        assert post_resp.status_code == 201
        snap_id = post_resp.json()["snapshot_id"]

        # List
        get_resp = await client_for_snapshots.get("/v1/events/replay/snapshots")
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["total"] >= 1
        ids = [s["snapshot_id"] for s in body["snapshots"]]
        assert snap_id in ids


class TestReplayArchiveProblemDetails:
    """Phase 13 route-local archive error mapping tests."""

    @pytest.mark.asyncio
    async def test_replay_archive_config_error_uses_route_local_problem_details(
        self,
        client_with_events: AsyncClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Invalid archive env maps to replay_archive_config_error, not /errors/internal."""
        monkeypatch.setenv("REPLAY_ARCHIVE_MANIFEST", str(tmp_path / "missing.json"))

        resp = await client_with_events.get("/v1/events/replay?to_sequence=5000")

        assert resp.status_code == 500
        body = resp.json()
        assert body["type"] == "/errors/replay-archive-config-error"
        assert body["title"] == "Replay archive configuration error"
        assert "missing" in body["detail"] or "does not exist" in body["detail"]
        assert body["extensions"]["code"] == "replay_archive_config_error"
        assert body["type"] != "/errors/internal"

    @pytest.mark.asyncio
    async def test_validate_archive_config_error_uses_route_local_problem_details(
        self,
        client_validate_matching: AsyncClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Validate endpoint uses the same route-local archive mapping."""
        monkeypatch.setenv("EVENT_LOG_ARCHIVE_MANIFEST", str(tmp_path / "missing.json"))

        resp = await client_validate_matching.get("/v1/events/replay/validate")

        assert resp.status_code == 500
        body = resp.json()
        assert body["type"] == "/errors/replay-archive-config-error"
        assert body["title"] == "Replay archive configuration error"
        assert body["extensions"]["code"] == "replay_archive_config_error"
        assert body["type"] != "/errors/internal"

    @pytest.mark.asyncio
    async def test_snapshot_create_ignores_invalid_archive_env(
        self,
        client_for_snapshots: AsyncClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Snapshot route remains archive-unaware when replay archive env is invalid."""
        monkeypatch.setenv("REPLAY_ARCHIVE_MANIFEST", str(tmp_path / "missing.json"))

        resp = await client_for_snapshots.post("/v1/events/replay/snapshots")

        assert resp.status_code == 201, resp.text
        assert resp.json()["sequence_number"] == 5000
