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


@pytest_asyncio.fixture(loop_scope="function")  # Story 8.7.6 PP2 — LifespanManager bg state
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


@pytest_asyncio.fixture(loop_scope="function")  # Story 8.7.6 PP2 — LifespanManager bg state
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


@pytest_asyncio.fixture(loop_scope="function")  # Story 8.7.6 PP2 — LifespanManager bg state
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


@pytest_asyncio.fixture(loop_scope="function")  # Story 8.7.6 PP2 — LifespanManager bg state
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


@pytest_asyncio.fixture(loop_scope="function")  # Story 8.7.6 PP2 — LifespanManager bg state
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
# Story 112.2: GET /v1/tasks/{task_id}/logs/digest/stream NDJSON boundary
# ---------------------------------------------------------------------------


def _ndjson_frames(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class TestDigestStreamBoundary:
    @pytest.mark.asyncio
    async def test_digest_stream_returns_bounded_ndjson_open_chunk_final_frames(
        self, digest_client: AsyncClient
    ) -> None:
        r = await digest_client.get(f"/v1/tasks/{_TID}/logs/digest/stream")

        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-ndjson")
        frames = _ndjson_frames(r.text)
        assert [frame["type"] for frame in frames] == ["open", "chunk", "final"]
        allowed_keys = {
            "type",
            "task_id",
            "route",
            "sequence",
            "chunk",
            "retrieved_at",
            "freshness_state",
            "display_state",
            "authority_state",
            "provenance",
            "request_id",
            "trace_id",
            "correlation_id",
            "truncated",
            "line_count",
            "chunk_count",
        }
        assert all(set(frame) <= allowed_keys for frame in frames)
        assert all(frame["task_id"] == _TID for frame in frames)
        assert frames[0]["route"] == "GET /v1/tasks/{task_id}/logs/digest/stream"
        assert frames[0]["display_state"] == "partial"
        assert frames[0]["authority_state"] == "non-authoritative"
        assert frames[1]["sequence"] == 1
        assert isinstance(frames[1]["chunk"], str)
        assert 1 <= len(str(frames[1]["chunk"])) <= 2_000
        assert frames[-1]["display_state"] == "healthy"
        assert frames[-1]["authority_state"] == "authoritative"
        assert frames[-1]["freshness_state"] == "fresh"
        line_count = frames[-1]["line_count"]
        assert isinstance(line_count, int)
        assert line_count >= 1

    @pytest.mark.asyncio
    async def test_digest_stream_rejects_query_selectors_and_request_body(
        self, digest_client: AsyncClient
    ) -> None:
        query = await digest_client.get(f"/v1/tasks/{_TID}/logs/digest/stream?mode=raw")
        body = await digest_client.request(
            "GET", f"/v1/tasks/{_TID}/logs/digest/stream", content=b'{"raw":true}'
        )

        assert query.status_code == 400
        assert body.status_code == 400
        assert "does not accept query" in query.text
        assert "does not accept a request body" in body.text

    @pytest.mark.asyncio
    async def test_digest_stream_404_when_no_events(self, no_events_client: AsyncClient) -> None:
        r = await no_events_client.get(f"/v1/tasks/{_TID_NO_EVENTS}/logs/digest/stream")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/problem+json")

    @pytest.mark.asyncio
    async def test_digest_stream_provider_unavailable_still_streams_bounded_fallback(
        self, no_client_configured: AsyncClient
    ) -> None:
        r = await no_client_configured.get(f"/v1/tasks/{_TID}/logs/digest/stream")

        assert r.status_code == 200
        frames = _ndjson_frames(r.text)
        assert [frame["type"] for frame in frames] == ["open", "chunk", "final"]
        assert frames[-1]["display_state"] == "provider-unavailable"
        assert frames[-1]["authority_state"] == "non-authoritative"
        rendered = " ".join(json.dumps(frame, sort_keys=True) for frame in frames).lower()
        assert "llm unavailable" in rendered
        for forbidden in (
            "description",
            "payload_json",
            "prompt",
            "anthropic",
            "openai",
            "provider_internal",
            "file://",
            "http://",
            "https://",
            "/users/",
            "retry",
            "control",
        ):
            assert forbidden not in rendered

    @pytest.mark.asyncio
    async def test_digest_stream_suppresses_overbroad_successful_llm_output(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_task_and_events(db_url, event_count=5)
        app = build_app(
            base_dir=tmp_path / "events",
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
            app.state.anthropic_client = _make_mock_anthropic_client(
                "Safe-looking summary with Payload_JSON, Provider Internals, raw logs, "
                "raw events, hrefs, URLs, source token, HTTPS://example.test/raw, "
                "\"/Users/operator/work\", '/tmp/operator/work', `/tmp/operator/work`, "
                '"C:\\tmp\\x", /home/operator/work, Prompts, OpenAI metadata, Retry, '
                "Control, and control hints."
            )
            r = await client.get(f"/v1/tasks/{_TID}/logs/digest/stream")

        assert r.status_code == 200
        frames = _ndjson_frames(r.text)
        assert [frame["type"] for frame in frames] == ["open", "chunk", "final"]
        assert frames[-1]["display_state"] == "invalid"
        assert frames[-1]["authority_state"] == "non-authoritative"
        rendered = " ".join(json.dumps(frame, sort_keys=True) for frame in frames).lower()
        assert "suppressed by safety boundary" in rendered
        for forbidden in (
            "payload_json",
            "provider internal",
            "raw log",
            "raw event",
            "href",
            "url",
            "source token",
            "https://",
            "/users/",
            "/tmp/",
            "/home/",
            "c:\\",
            "prompt",
            "openai",
            "retry",
            "control",
        ):
            assert forbidden not in rendered

    @pytest.mark.asyncio
    async def test_digest_stream_chunks_are_bounded_for_large_digest(self, tmp_path: Path) -> None:
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_task_and_events(db_url, event_count=5)
        app = build_app(
            base_dir=tmp_path / "events",
            db_url=db_url,
            clock=_FROZEN_CLOCK,
        )
        app.state.anthropic_client = _make_mock_anthropic_client(
            "\n".join(f"Line {i}" for i in range(25))
        )

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app),
                base_url="http://testserver",
            ) as client,
        ):
            r = await client.get(f"/v1/tasks/{_TID}/logs/digest/stream")

        assert r.status_code == 200
        frames = _ndjson_frames(r.text)
        chunks = [frame for frame in frames if frame["type"] == "chunk"]
        assert 1 <= len(chunks) <= 10
        assert all(1 <= len(str(frame["chunk"])) <= 2_000 for frame in chunks)
        line_count = frames[-1]["line_count"]
        assert frames[-1]["chunk_count"] == len(chunks)
        assert isinstance(line_count, int)
        assert line_count <= 20

    @pytest.mark.asyncio
    async def test_digest_stream_exactly_ten_chunks_is_not_marked_truncated(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_task_and_events(db_url, event_count=5)
        app = build_app(
            base_dir=tmp_path / "events",
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
            app.state.anthropic_client = _make_mock_anthropic_client("x" * 20_000)
            r = await client.get(f"/v1/tasks/{_TID}/logs/digest/stream")

        assert r.status_code == 200
        frames = _ndjson_frames(r.text)
        chunks = [frame for frame in frames if frame["type"] == "chunk"]
        assert len(chunks) == 10
        assert frames[-1]["chunk_count"] == 10
        assert frames[-1]["truncated"] is False


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
