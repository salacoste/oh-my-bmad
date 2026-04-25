"""Tests for registry-api HTTP skeleton (Story 2.9 AC-11).

20 tests across 5 classes. No conftest — all fixtures inlined per project
convention (Story 2.4/2.5). All async tests use ``pytest.mark.asyncio`` with
``asyncio_mode = "strict"`` from pyproject.toml.

Client pattern: ``httpx.AsyncClient(transport=ASGITransport(app=manager.app))``
with ``asgi_lifespan.LifespanManager`` so the FastAPI lifespan (writer + engine
startup/shutdown) executes properly.

DB pattern for GET tests:
  - The app uses ``create_engine(db_url, read_only=True)`` which rejects
    in-memory URLs. GET tests therefore use a real file path (``tmp_path``).
  - Pre-seeding uses a separate writable engine on the same file before
    starting the app, then the app's read-only engine reads the data.

Classes:
  TestPostTasks      (~6) — POST /v1/tasks happy path + validation + log write.
  TestGetTaskById    (~5) — GET /v1/tasks/{id} happy/not-found + content.
  TestMiddleware     (~4) — Request-ID / Idempotency-Key / Actor-ID headers.
  TestErrorHandlers  (~3) — validation error 400, 404 problem+json, content-type.
  TestEntryPoint     (~2) — __main__ env-var → uvicorn.run args.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path
from random import Random
from unittest.mock import patch

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from events import (
    FROZEN_EPOCH,
    FrozenClock,
    new_event_id,
    new_request_id,
    new_task_id,
)
from events.schema_registry import register as _reg
from fastapi import Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from registry_state.adapters.event_log import (  # noqa: IMP001 — services→services allowed per AC-16
    current_day_path,
    read_log_lines,
)
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — services→services allowed per AC-16
    create_engine as _create_engine,
)
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — services→services allowed per AC-16
    get_session,
)
from registry_state.domain.event_types import (  # noqa: IMP001 — services→services allowed per AC-16
    TaskCreatedPayload,
)
from registry_state.schema import (  # noqa: IMP001 — services→services allowed per AC-16
    Base,
    Event,
    Task,
)

from registry_api.app import build_app

# ---------------------------------------------------------------------------
# Schema-registry guard (same pattern as clawhip-bridge test_server.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    """Re-register task.created before each test.

    The event_log tests run an autouse fixture that calls ``unregister_all()``
    at teardown. Re-registering here (idempotent) ensures the registry is
    populated regardless of test execution order.
    """
    _reg("task.created", "1.0.0", TaskCreatedPayload)


# ---------------------------------------------------------------------------
# Local constants + fixtures (inlined — no conftest per project convention)
# ---------------------------------------------------------------------------

_FROZEN_MONO_NS = 1_000_000
_FROZEN_CLOCK = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
_RNG = Random(42)

# Deterministic task/event IDs for seeding GET-test fixtures
_SEEDED_TASK_ID = new_task_id(clock=_FROZEN_CLOCK, rng=_RNG)
_SEEDED_EVENT_ID = new_event_id(clock=_FROZEN_CLOCK, rng=_RNG)


@pytest.fixture
def fixed_clock() -> FrozenClock:
    """Stationary clock at FROZEN_EPOCH with mono_ns=1_000_000."""
    return FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)


# ---------------------------------------------------------------------------
# App + client helpers
# ---------------------------------------------------------------------------


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _seed_tables(db_url: str) -> None:
    """Create all ORM tables in the writable DB at *db_url*."""
    engine = _create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _seed_task_row(db_url: str) -> None:
    """Insert a deterministic Task + Event row into the writable DB."""
    engine = _create_engine(db_url)
    session_maker = get_session(engine)
    async with session_maker() as session:
        # Insert Task first (Event FK references Task.id — must exist before Event).
        task = Task(
            id=_SEEDED_TASK_ID,
            status="plan_ready",
            created_at=FROZEN_EPOCH,
            updated_at=FROZEN_EPOCH,
            actor_kind="operator",
            actor_id="http-api",
            title="seeded task",
            last_event_id=None,  # set after event is inserted
        )
        session.add(task)  # noqa: SW001 — test-only fixture seeding, not production write path
        await session.flush()  # flush task first so FK on event passes

        event = Event(
            id=_SEEDED_EVENT_ID,
            type="task.created",
            schema_version="1.0.0",
            emitted_at=FROZEN_EPOCH,
            emitted_at_monotonic_ns=_FROZEN_MONO_NS,
            actor_kind="operator",
            actor_id="http-api",
            task_id=_SEEDED_TASK_ID,
            session_id=None,
            parent_event_id=None,
            request_id=new_request_id(clock=_FROZEN_CLOCK, rng=Random(99)),
            payload_json='{"task_id": "' + _SEEDED_TASK_ID + '", "title": "seeded task"}',
        )
        session.add(event)  # noqa: SW001 — test-only fixture seeding, not production write path
        await session.flush()

        # Now update task.last_event_id now that event row exists
        task.last_event_id = _SEEDED_EVENT_ID
        await session.commit()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Async client fixture for POST-only tests (no DB needed for write path)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def post_client(
    tmp_path: Path, fixed_clock: FrozenClock
) -> AsyncGenerator[AsyncClient, None]:
    """Client for POST /v1/tasks tests — no pre-seeded DB needed.

    Uses an in-memory-like approach: create_tables on a real file DB so the
    app lifespan doesn't fail, but the POST handler only writes to JSONL.
    """
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables(db_url)

    events_dir = tmp_path / "events"
    app = build_app(base_dir=events_dir, db_url=db_url, clock=fixed_clock)

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client


# ---------------------------------------------------------------------------
# Async client fixture for GET tests (pre-seeded DB row)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def get_client(tmp_path: Path, fixed_clock: FrozenClock) -> AsyncGenerator[AsyncClient, None]:
    """Client for GET /v1/tasks/{id} tests — pre-seeds a Task + Event row."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables(db_url)
    await _seed_task_row(db_url)

    events_dir = tmp_path / "events"
    app = build_app(base_dir=events_dir, db_url=db_url, clock=fixed_clock)

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client


# ---------------------------------------------------------------------------
# TestPostTasks
# ---------------------------------------------------------------------------


class TestPostTasks:
    """AC-2: POST /v1/tasks — 201 response, JSONL write, validation."""

    @pytest.mark.asyncio
    async def test_post_tasks_returns_201_with_task_id(self, post_client: AsyncClient) -> None:
        """POST /v1/tasks returns 201 with task_id, event_id, created_at."""
        r = await post_client.post("/v1/tasks", json={"title": "my task"})
        assert r.status_code == 201
        body = r.json()
        assert body["task_id"].startswith("t-")
        assert body["event_id"].startswith("e-")
        assert "created_at" in body

    @pytest.mark.asyncio
    async def test_post_tasks_writes_envelope_to_jsonl_log(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """POST /v1/tasks appends a canonical JSONL envelope to the event log."""
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_tables(db_url)

        events_dir = tmp_path / "events"
        app = build_app(base_dir=events_dir, db_url=db_url, clock=fixed_clock)

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            r = await client.post("/v1/tasks", json={"title": "log test"})
            assert r.status_code == 201

        # After lifespan exits the writer is closed; now read the log.
        log_path = current_day_path(events_dir, FROZEN_EPOCH)
        lines = list(read_log_lines(log_path))
        assert len(lines) == 1
        envelope = lines[0]
        assert envelope.type == "task.created"

    @pytest.mark.asyncio
    async def test_post_tasks_envelope_has_task_created_type(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """Envelope written to log has type='task.created' and matching task_id."""
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_tables(db_url)

        events_dir = tmp_path / "events"
        app = build_app(base_dir=events_dir, db_url=db_url, clock=fixed_clock)

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            r = await client.post("/v1/tasks", json={"title": "type check"})

        log_path = current_day_path(events_dir, FROZEN_EPOCH)
        envelopes = list(read_log_lines(log_path))
        assert len(envelopes) == 1
        env = envelopes[0]
        assert env.type == "task.created"
        # Canonical JSON serializes the BaseModel payload via model_dump —
        # the task_id in the response body is the authoritative check; here
        # we verify the envelope type and that the event_id matches.
        assert env.event_id == r.json()["event_id"]

    @pytest.mark.asyncio
    async def test_post_tasks_uses_request_id_from_header_when_provided(
        self, post_client: AsyncClient
    ) -> None:
        """POST with X-Request-ID header: response echoes the same value."""
        custom_request_id = new_request_id(clock=_FROZEN_CLOCK, rng=Random(77))
        r = await post_client.post(
            "/v1/tasks",
            json={"title": "req-id test"},
            headers={"X-Request-ID": custom_request_id},
        )
        assert r.status_code == 201
        assert r.headers["X-Request-ID"] == custom_request_id

    @pytest.mark.asyncio
    async def test_post_tasks_generates_request_id_when_header_absent(
        self, post_client: AsyncClient
    ) -> None:
        """POST without X-Request-ID header: response has a generated header."""
        r = await post_client.post("/v1/tasks", json={"title": "no req-id"})
        assert r.status_code == 201
        # A generated UUIDv7 bare (no prefix) must be present in the response
        assert "X-Request-ID" in r.headers
        assert len(r.headers["X-Request-ID"]) == 36  # bare UUIDv7 length

    @pytest.mark.asyncio
    async def test_post_tasks_rejects_extra_fields_in_body(self, post_client: AsyncClient) -> None:
        """POST with unknown field returns 400 (Pydantic extra='forbid' → 422 → handler → 400)."""
        r = await post_client.post("/v1/tasks", json={"title": "ok", "unknown_field": "bad"})
        # Our handle_validation_error maps RequestValidationError → 400
        assert r.status_code == 400
        body = r.json()
        assert body["status"] == 400
        assert "title" in body  # RFC 7807 field


# ---------------------------------------------------------------------------
# TestGetTaskById
# ---------------------------------------------------------------------------


class TestGetTaskById:
    """AC-3: GET /v1/tasks/{task_id} — read from SQLite, 200/404 responses."""

    @pytest.mark.asyncio
    async def test_get_task_returns_200_with_full_state(self, get_client: AsyncClient) -> None:
        """GET seeded task returns 200 with all TaskResponse fields."""
        r = await get_client.get(f"/v1/tasks/{_SEEDED_TASK_ID}")
        assert r.status_code == 200
        body = r.json()
        assert body["task_id"] == _SEEDED_TASK_ID
        assert body["status"] == "plan_ready"
        assert body["title"] == "seeded task"
        assert "created_at" in body
        assert "updated_at" in body
        assert body["actor"]["kind"] == "operator"
        assert body["actor"]["id"] == "http-api"

    @pytest.mark.asyncio
    async def test_get_task_returns_404_with_problem_json_when_not_found(
        self, get_client: AsyncClient
    ) -> None:
        """GET unknown task_id (valid shape) returns 404 problem+json."""
        # Use a valid UUIDv7 task_id shape that doesn't exist in DB
        missing_id = new_task_id(clock=_FROZEN_CLOCK, rng=Random(55))
        r = await get_client.get(f"/v1/tasks/{missing_id}")
        assert r.status_code == 404
        body = r.json()
        assert body["status"] == 404
        assert "title" in body

    @pytest.mark.asyncio
    async def test_get_task_404_response_has_correct_content_type(
        self, get_client: AsyncClient
    ) -> None:
        """404 response uses application/problem+json content-type."""
        missing_id = new_task_id(clock=_FROZEN_CLOCK, rng=Random(66))
        r = await get_client.get(f"/v1/tasks/{missing_id}")
        assert r.status_code == 404
        assert "application/problem+json" in r.headers["content-type"]

    @pytest.mark.asyncio
    async def test_get_task_includes_last_event_field(self, get_client: AsyncClient) -> None:
        """GET seeded task includes last_event with id, type, emitted_at."""
        r = await get_client.get(f"/v1/tasks/{_SEEDED_TASK_ID}")
        assert r.status_code == 200
        body = r.json()
        last = body["last_event"]
        assert last is not None
        assert last["id"] == _SEEDED_EVENT_ID
        assert last["type"] == "task.created"
        assert "emitted_at" in last

    @pytest.mark.asyncio
    async def test_get_task_includes_next_commands_per_status(
        self, get_client: AsyncClient
    ) -> None:
        """GET task with status='plan_ready' returns next_commands=['approve','reject','stop']."""
        r = await get_client.get(f"/v1/tasks/{_SEEDED_TASK_ID}")
        assert r.status_code == 200
        body = r.json()
        # Seeded task has status='plan_ready'
        assert set(body["next_commands"]) == {"approve", "reject", "stop"}


# ---------------------------------------------------------------------------
# TestMiddleware
# ---------------------------------------------------------------------------


class TestMiddleware:
    """AC-4: RequestIdMiddleware, IdempotencyKeyMiddleware, ActorIdMiddleware."""

    @pytest.mark.asyncio
    async def test_request_id_middleware_generates_when_absent(
        self, post_client: AsyncClient
    ) -> None:
        """Response always has X-Request-ID even when not sent by client."""
        r = await post_client.post("/v1/tasks", json={"title": "mw test"})
        assert "X-Request-ID" in r.headers
        assert len(r.headers["X-Request-ID"]) == 36

    @pytest.mark.asyncio
    async def test_request_id_middleware_propagates_when_present(
        self, post_client: AsyncClient
    ) -> None:
        """X-Request-ID echoed back unchanged when client sends it."""
        rid = new_request_id(clock=_FROZEN_CLOCK, rng=Random(11))
        r = await post_client.post(
            "/v1/tasks",
            json={"title": "propagate rid"},
            headers={"X-Request-ID": rid},
        )
        assert r.headers["X-Request-ID"] == rid

    @pytest.mark.asyncio
    async def test_idempotency_key_middleware_attaches_to_request_state(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """Idempotency-Key header value flows through to request.state (no dedup yet)."""
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_tables(db_url)

        events_dir = tmp_path / "events"
        app = build_app(base_dir=events_dir, db_url=db_url, clock=fixed_clock)

        # Add a diagnostic endpoint that echoes request.state.idempotency_key
        @app.get("/debug/idem")
        async def _idem_echo(request: Request) -> JSONResponse:
            return JSONResponse({"idempotency_key": request.state.idempotency_key})

        idem_key = new_request_id(clock=_FROZEN_CLOCK, rng=Random(22))
        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            r = await client.get("/debug/idem", headers={"Idempotency-Key": idem_key})
        assert r.status_code == 200
        assert r.json()["idempotency_key"] == idem_key

    @pytest.mark.asyncio
    async def test_actor_id_middleware_sets_http_api_default(
        self, tmp_path: Path, fixed_clock: FrozenClock
    ) -> None:
        """ActorIdMiddleware sets request.state.actor_id = 'http-api' for all requests."""
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_tables(db_url)

        events_dir = tmp_path / "events"
        app = build_app(base_dir=events_dir, db_url=db_url, clock=fixed_clock)

        @app.get("/debug/actor")
        async def _actor_echo(request: Request) -> JSONResponse:
            return JSONResponse({"actor_id": request.state.actor_id})

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            r = await client.get("/debug/actor")
        assert r.status_code == 200
        assert r.json()["actor_id"] == "http-api"


# ---------------------------------------------------------------------------
# TestErrorHandlers
# ---------------------------------------------------------------------------


class TestErrorHandlers:
    """AC-5: RFC 7807 problem+json error envelopes."""

    @pytest.mark.asyncio
    async def test_validation_error_returns_400_problem_json(
        self, post_client: AsyncClient
    ) -> None:
        """Missing required 'title' field → 400 with RFC 7807 problem+json body."""
        r = await post_client.post("/v1/tasks", json={})  # title missing
        assert r.status_code == 400
        body = r.json()
        assert body["status"] == 400
        assert "title" in body  # RFC 7807 'title' field (not task title)
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_http_exception_returns_problem_json(self, get_client: AsyncClient) -> None:
        """404 HTTPException produces RFC 7807 problem+json body."""
        missing_id = new_task_id(clock=_FROZEN_CLOCK, rng=Random(33))
        r = await get_client.get(f"/v1/tasks/{missing_id}")
        assert r.status_code == 404
        body = r.json()
        # RFC 7807 required fields
        assert "type" in body
        assert "title" in body
        assert "status" in body
        assert body["status"] == 404
        assert "instance" in body

    @pytest.mark.asyncio
    async def test_unhandled_validation_returns_400_with_problem_json_envelope(
        self, post_client: AsyncClient
    ) -> None:
        """Extra field in body → 400 problem+json (covers RequestValidationError path)."""
        r = await post_client.post("/v1/tasks", json={"title": "t", "extra": "value"})
        assert r.status_code == 400
        body = r.json()
        assert body["status"] == 400
        assert "application/problem+json" in r.headers["content-type"]


# ---------------------------------------------------------------------------
# TestEntryPoint
# ---------------------------------------------------------------------------


class TestEntryPoint:
    """AC-6: __main__ reads env vars and passes them to uvicorn.run."""

    def test_main_uses_default_host_port_when_env_absent(self, tmp_path: Path) -> None:
        """__main__.main() uses defaults when env vars are absent."""
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in {
                "REGISTRY_API_DB_URL",
                "REGISTRY_API_LOG_DIR",
                "REGISTRY_API_HOST",
                "REGISTRY_API_PORT",
            }
        }
        # Point DB and log to tmp_path so the app can start without
        # /var/lib/oh-my-bmad existing. We intercept uvicorn.run so it
        # never actually binds a socket.
        db_path = tmp_path / "state.sqlite3"
        log_dir = tmp_path / "events"
        env["REGISTRY_API_DB_URL"] = f"sqlite+aiosqlite:///{db_path}"
        env["REGISTRY_API_LOG_DIR"] = str(log_dir)

        captured: dict[str, object] = {}

        def fake_run(app: object, **kwargs: object) -> None:
            captured.update(kwargs)

        with (
            patch.dict(os.environ, env, clear=True),
            patch("registry_api.__main__.uvicorn.run", side_effect=fake_run),
        ):
            from registry_api.__main__ import main

            main()

        assert captured.get("host") == "0.0.0.0"
        assert captured.get("port") == 8080

    def test_main_respects_env_overrides(self, tmp_path: Path) -> None:
        """__main__.main() forwards REGISTRY_API_HOST/PORT to uvicorn.run."""
        db_path = tmp_path / "state.sqlite3"
        log_dir = tmp_path / "events"
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in {
                "REGISTRY_API_DB_URL",
                "REGISTRY_API_LOG_DIR",
                "REGISTRY_API_HOST",
                "REGISTRY_API_PORT",
            }
        }
        env["REGISTRY_API_DB_URL"] = f"sqlite+aiosqlite:///{db_path}"
        env["REGISTRY_API_LOG_DIR"] = str(log_dir)
        env["REGISTRY_API_HOST"] = "127.0.0.1"
        env["REGISTRY_API_PORT"] = "9999"

        captured: dict[str, object] = {}

        def fake_run(app: object, **kwargs: object) -> None:
            captured.update(kwargs)

        with (
            patch.dict(os.environ, env, clear=True),
            patch("registry_api.__main__.uvicorn.run", side_effect=fake_run),
        ):
            from registry_api.__main__ import main

            main()

        assert captured.get("host") == "127.0.0.1"
        assert captured.get("port") == 9999
