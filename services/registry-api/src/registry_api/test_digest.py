"""Tests for GET /v1/tasks/{task_id}/logs/digest (Story 7.3).

Covers:
  - Digest with mocked Anthropic client (happy path)
  - Truncation on large input (>50 events)
  - 404 when no events exist for the task
  - Fallback digest when Anthropic API raises APIError
  - Wire contract: response JSON keys match LogsDigestResponseLocal
  - Fallback when no Anthropic client configured (client=None)
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from httpx import ASGITransport, AsyncClient
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 test fixture builds in-memory SQLite via registry-state's schema; no prod cross-service coupling
    create_engine,
)
from registry_state.schema import (  # noqa: IMP001 test fixture imports tables for Base.metadata.create_all seeding
    Base,
    Event,
    Task,
)

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
    event_count: int = 25,
) -> list[str]:
    """Seed a task + N events. Returns list of event IDs."""
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        await conn.execute(
            Task.__table__.insert(),  # type: ignore[attr-defined]  # SQLAlchemy stubs return FromClause; Table.__table__ resolves at runtime
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
            emitted = datetime(2026, 1, 1, 10, i % 60, tzinfo=UTC)
            payload = json.dumps(
                {
                    "task_id": task_id,
                    "description": f"Event {i} description",
                }
            )
            await conn.execute(
                Event.__table__.insert(),  # type: ignore[attr-defined]  # SQLAlchemy stubs return FromClause; Table.__table__ resolves at runtime
                {
                    "id": eid,
                    "type": ("task.blocker_raised" if i % 5 == 0 else "file.edited"),
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
            Task.__table__.update()  # type: ignore[attr-defined]  # SQLAlchemy stubs return FromClause; Table.__table__ resolves at runtime
            .where(Task.__table__.c.id == task_id)
            .values(last_event_id=event_ids[-1])
        )

    await engine.dispose()
    return event_ids


async def _seed_task_only(db_url: str, task_id: str) -> None:
    """Seed a task with no events (for 404 test)."""
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        await conn.execute(
            Task.__table__.insert(),  # type: ignore[attr-defined]  # SQLAlchemy stubs return FromClause; Table.__table__ resolves at runtime
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
# Mock Anthropic client factory
# ---------------------------------------------------------------------------


def _make_mock_anthropic_client(response_text: str = "Line 1\nLine 2\nLine 3") -> AsyncMock:
    """Build a mock AsyncAnthropic that returns canned text."""
    mock_client = AsyncMock()

    # Mock the response structure from Anthropic SDK
    text_block = AsyncMock()
    text_block.type = "text"
    text_block.text = response_text

    response = AsyncMock()
    response.content = [text_block]
    mock_client.messages.create = AsyncMock(return_value=response)
    return mock_client


def _make_failing_anthropic_client() -> AsyncMock:
    """Build a mock AsyncAnthropic that raises APIError."""
    import anthropic

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(
        side_effect=anthropic.APIError(
            message="test error",
            request=AsyncMock(),
            body=None,
        )
    )
    return mock_client


# ---------------------------------------------------------------------------
# Client fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def digest_client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    """Client with 25 seeded events and mock Anthropic client."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_task_and_events(db_url, event_count=25)

    events_dir = tmp_path / "events"
    app = build_app(
        base_dir=events_dir,
        db_url=db_url,
        clock=_FROZEN_CLOCK,
    )
    # Inject mock Anthropic client.
    mock_client = _make_mock_anthropic_client(
        "Summary of key events:\n- Task started\n- Blocker raised at 10:00\n- File edited at 10:04"
    )

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app),
            base_url="http://testserver",
        ) as client,
    ):
        app.state.anthropic_client = mock_client
        yield client


@pytest_asyncio.fixture
async def large_digest_client(
    tmp_path: Path,
) -> AsyncGenerator[AsyncClient, None]:
    """Client with 100+ seeded events for truncation test."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_task_and_events(db_url, event_count=120)

    events_dir = tmp_path / "events"
    app = build_app(
        base_dir=events_dir,
        db_url=db_url,
        clock=_FROZEN_CLOCK,
    )
    mock_client = _make_mock_anthropic_client("Truncated summary line.")

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app),
            base_url="http://testserver",
        ) as client,
    ):
        app.state.anthropic_client = mock_client
        yield client


@pytest_asyncio.fixture
async def no_events_client(
    tmp_path: Path,
) -> AsyncGenerator[AsyncClient, None]:
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


@pytest_asyncio.fixture
async def fallback_client(
    tmp_path: Path,
) -> AsyncGenerator[AsyncClient, None]:
    """Client with events but Anthropic client that raises APIError."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_task_and_events(db_url, event_count=10)

    events_dir = tmp_path / "events"
    app = build_app(
        base_dir=events_dir,
        db_url=db_url,
        clock=_FROZEN_CLOCK,
    )
    mock_client = _make_failing_anthropic_client()

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app),
            base_url="http://testserver",
        ) as client,
    ):
        app.state.anthropic_client = mock_client
        yield client


@pytest_asyncio.fixture
async def no_client_configured(
    tmp_path: Path,
) -> AsyncGenerator[AsyncClient, None]:
    """Client with events but anthropic_client=None (no API key)."""
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
        app.state.anthropic_client = None
        yield client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDigestHappyPath:
    @pytest.mark.asyncio
    async def test_digest_returns_summary_with_events(self, digest_client: AsyncClient) -> None:
        """AC #1: digest endpoint returns summary with line_count >= 1."""
        r = await digest_client.get(f"/v1/tasks/{_TID}/logs/digest")
        assert r.status_code == 200
        body = r.json()
        assert body["task_id"] == _TID
        assert body["digest"]
        assert body["line_count"] >= 1
        assert "truncated" in body


class TestDigestTruncation:
    @pytest.mark.asyncio
    async def test_digest_truncates_on_large_input(self, large_digest_client: AsyncClient) -> None:
        """AC #2: 100+ events → truncated=True."""
        r = await large_digest_client.get(f"/v1/tasks/{_TID}/logs/digest")
        assert r.status_code == 200
        body = r.json()
        assert body["truncated"] is True


class TestDigestNotFound:
    @pytest.mark.asyncio
    async def test_digest_404_when_no_events(self, no_events_client: AsyncClient) -> None:
        """AC: no events for task → 404 with RFC 7807 envelope."""
        r = await no_events_client.get(f"/v1/tasks/{_TID_NO_EVENTS}/logs/digest")
        assert r.status_code == 404


class TestDigestFallback:
    @pytest.mark.asyncio
    async def test_digest_fallback_on_anthropic_error(self, fallback_client: AsyncClient) -> None:
        """AC #2: Anthropic APIError → fallback digest (no crash)."""
        r = await fallback_client.get(f"/v1/tasks/{_TID}/logs/digest")
        assert r.status_code == 200
        body = r.json()
        assert body["task_id"] == _TID
        assert "raw event summary" in body["digest"]
        assert body["line_count"] >= 1

    @pytest.mark.asyncio
    async def test_digest_fallback_when_no_client(self, no_client_configured: AsyncClient) -> None:
        """No Anthropic client (no API key) → fallback digest."""
        r = await no_client_configured.get(f"/v1/tasks/{_TID}/logs/digest")
        assert r.status_code == 200
        body = r.json()
        assert "LLM unavailable" in body["digest"]


class TestWireContract:
    @pytest.mark.asyncio
    async def test_wire_contract_matches_local_model(self, digest_client: AsyncClient) -> None:
        """Response JSON keys must match LogsDigestResponseLocal fields."""
        r = await digest_client.get(f"/v1/tasks/{_TID}/logs/digest")
        assert r.status_code == 200
        body = r.json()
        # Exact keys from LogsDigestResponseLocal:
        # task_id, digest, truncated, line_count
        assert set(body.keys()) == {
            "task_id",
            "digest",
            "truncated",
            "line_count",
        }
        # Field constraints from LogsDigestResponseLocal
        assert 1 <= len(body["task_id"]) <= 128
        assert 1 <= len(body["digest"]) <= 20_000
        assert isinstance(body["truncated"], bool)
        assert 1 <= body["line_count"] <= 20


class TestLineCountBoundary:
    @pytest.mark.asyncio
    async def test_llm_returns_over_20_lines_clamped(self, tmp_path: Path) -> None:
        """LLM returning >20 lines → response clamped to line_count <= 20."""
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_task_and_events(db_url, event_count=5)

        events_dir = tmp_path / "events"
        app = build_app(
            base_dir=events_dir,
            db_url=db_url,
            clock=_FROZEN_CLOCK,
        )
        mock_client = _make_mock_anthropic_client("\n".join(f"Line {i}" for i in range(25)))

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app),
                base_url="http://testserver",
            ) as client,
        ):
            app.state.anthropic_client = mock_client
            r = await client.get(f"/v1/tasks/{_TID}/logs/digest")

        assert r.status_code == 200
        body = r.json()
        assert body["line_count"] <= 20
        assert body["line_count"] >= 1
        assert body["truncated"] is True

    @pytest.mark.asyncio
    async def test_fallback_digest_stays_within_20_lines(
        self, fallback_client: AsyncClient
    ) -> None:
        """Fallback digest with 10 events → line_count <= 20."""
        r = await fallback_client.get(f"/v1/tasks/{_TID}/logs/digest")
        assert r.status_code == 200
        body = r.json()
        assert body["line_count"] <= 20
        assert body["line_count"] >= 1


# ---------------------------------------------------------------------------
# Story 7.5.4: Configurable model + malformed timestamp sentinel
# ---------------------------------------------------------------------------


class TestConfigurableModel:
    """AC-1: Anthropic model name read from ANTHROPIC_MODEL env var."""

    def test_model_name_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from registry_api.adapters.llm_digest import _get_model

        monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-6-20250514")
        assert _get_model() == "claude-sonnet-4-6-20250514"

    def test_model_name_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from registry_api.adapters.llm_digest import _get_model

        monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
        assert _get_model() == "claude-haiku-4-5-20251001"

    def test_model_name_empty_string_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from registry_api.adapters.llm_digest import _get_model

        monkeypatch.setenv("ANTHROPIC_MODEL", "")
        assert _get_model() == "claude-haiku-4-5-20251001"


class TestMalformedTimestampSentinel:
    """AC-2: malformed ISO timestamps produce [invalid-timestamp]."""

    @pytest.fixture()
    def _ev(self) -> type:
        from registry_api.adapters.llm_digest import EventRow

        return EventRow

    def test_format_event_empty_timestamp(self, _ev: type) -> None:
        from registry_api.adapters.llm_digest import _format_event

        ev = _ev(type="task.blocker_raised", emitted_at_iso="", payload_json="{}")
        result = _format_event(ev)
        assert result.startswith("[invalid-timestamp]")

    def test_format_event_truncated_timestamp(self, _ev: type) -> None:
        from registry_api.adapters.llm_digest import _format_event

        ev = _ev(type="task.blocker_raised", emitted_at_iso="2026-01-01T10", payload_json="{}")
        result = _format_event(ev)
        assert result.startswith("[invalid-timestamp]")

    def test_format_event_valid_timestamp(self, _ev: type) -> None:
        from registry_api.adapters.llm_digest import _format_event

        ev = _ev(
            type="task.blocker_raised",
            emitted_at_iso="2026-01-01T10:30:00Z",
            payload_json='{"reason": "test"}',
        )
        result = _format_event(ev)
        assert result.startswith("[10:30]")
