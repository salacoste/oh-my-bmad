"""Tests for /v1/approvals/inbox endpoints (Story 11.3 / FR63).

Two endpoint surfaces:

* ``POST /v1/approvals/inbox`` — emit ``approval.inbox_opened`` to the
  JSONL event log. Used by the telegram-gateway ``/approvals`` handler.
* ``GET /v1/approvals/inbox/{operator_chat_id}`` — read the materialized
  ``ApprovalInbox`` row. Used by clawhip-daemon's outbound delivery sink
  to route ``task.approval_requested`` events into the pinned thread.

Tests cover:

* POST returns 201 with the expected body and ``X-Idempotency-Status:
  applied`` on first call; ``replayed`` on Idempotency-Key replay.
* POST appends a single ``approval.inbox_opened`` envelope to the JSONL
  log with the supplied chat / thread ids.
* POST rejects negative / zero ``inbox_thread_id`` with 422.
* GET returns 404 when no row exists for *operator_chat_id*.
* GET returns 200 with the full row when seeded.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from events.ids import new_idempotency_key
from httpx import ASGITransport, AsyncClient
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — test fixture builds in-memory SQLite via registry-state's schema; no prod cross-service coupling
    create_engine,
)
from registry_state.schema import (  # noqa: IMP001 — test fixture imports ORM models for Base.metadata.create_all seeding
    ApprovalInbox,
    Base,
)

from registry_api.app import build_app


@pytest_asyncio.fixture
async def app_client(
    tmp_path: Path,
) -> AsyncGenerator[tuple[AsyncClient, Path, Path, object], None]:
    """Build an in-process registry-api with a fresh SQLite + JSONL store.

    Yields ``(client, db_path, events_dir, session_maker)``: tests use the
    same ``session_maker`` the GET endpoint uses so writer + reader share
    the lifespan-owned engine (Story 11.3 review P11 — avoids racing a
    second engine on the same SQLite file).
    """
    db_path = tmp_path / "state.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    # Create schema (all tables including approval_inbox via migration 0007).
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    events_dir = tmp_path / "events"
    clock = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)
    app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        # ``app.state.session_maker`` is wired during lifespan startup
        # (LifespanManager runs the lifespan callable but does NOT wrap
        # the FastAPI instance itself, so we reference the outer ``app``).
        session_maker = app.state.session_maker
        yield client, db_path, events_dir, session_maker


def _read_jsonl_envelopes(events_dir: Path) -> list[dict[str, object]]:
    """Drain every JSONL file under *events_dir* and return parsed envelopes."""
    if not events_dir.exists():
        return []
    envelopes: list[dict[str, object]] = []
    for path in sorted(events_dir.iterdir()):
        if not path.is_file() or not path.name.endswith(".jsonl"):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                envelopes.append(json.loads(line))
    return envelopes


# ---------------------------------------------------------------------------
# POST /v1/approvals/inbox tests
# ---------------------------------------------------------------------------


class TestPostInbox:
    @pytest.mark.asyncio
    async def test_post_returns_201_and_emits_event(
        self,
        app_client: tuple[AsyncClient, Path, Path, object],
    ) -> None:
        """First POST → 201 with applied status; JSONL has exactly one envelope."""
        client, _, events_dir, _ = app_client
        r = await client.post(
            "/v1/approvals/inbox",
            json={"operator_chat_id": -1001234567890, "inbox_thread_id": 42},
            headers={"Idempotency-Key": new_idempotency_key()},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["operator_chat_id"] == -1001234567890
        assert body["inbox_thread_id"] == 42
        assert body["event_id"].startswith("e-")
        assert body["idempotency_status"] == "applied"
        assert "opened_at" in body
        assert r.headers["x-idempotency-status"] == "applied"

        envelopes = _read_jsonl_envelopes(events_dir)
        assert len(envelopes) == 1
        env = envelopes[0]
        assert env["type"] == "approval.inbox_opened"
        assert env["schema_version"] == "1.1.0"
        payload = env["payload"]
        assert isinstance(payload, dict)
        assert payload["operator_chat_id"] == -1001234567890
        assert payload["inbox_thread_id"] == 42

    @pytest.mark.asyncio
    async def test_post_is_idempotent_on_replay(
        self,
        app_client: tuple[AsyncClient, Path, Path, object],
    ) -> None:
        """Same Idempotency-Key → 201 with X-Idempotency-Status: replayed; only one event."""
        client, _, events_dir, _ = app_client
        body_json = {"operator_chat_id": -1001234567890, "inbox_thread_id": 42}
        key = new_idempotency_key()
        headers = {"Idempotency-Key": key}

        r1 = await client.post("/v1/approvals/inbox", json=body_json, headers=headers)
        r2 = await client.post("/v1/approvals/inbox", json=body_json, headers=headers)

        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.headers["x-idempotency-status"] == "applied"
        assert r2.headers["x-idempotency-status"] == "replayed"
        # Story 11.3 review P13: the body's ``idempotency_status`` field
        # now mirrors the header on replay (was always "applied" pre-P13).
        body1 = r1.json()
        body2 = r2.json()
        assert body1["idempotency_status"] == "applied"
        assert body2["idempotency_status"] == "replayed"
        # The non-status fields are byte-identical on replay (event_id,
        # opened_at, operator_chat_id, inbox_thread_id all match).
        body1_no_status = {k: v for k, v in body1.items() if k != "idempotency_status"}
        body2_no_status = {k: v for k, v in body2.items() if k != "idempotency_status"}
        assert body1_no_status == body2_no_status

        envelopes = _read_jsonl_envelopes(events_dir)
        assert len(envelopes) == 1, "Idempotency dedup must not append a second envelope"

    @pytest.mark.asyncio
    async def test_post_rejects_zero_inbox_thread_id(
        self,
        app_client: tuple[AsyncClient, Path, Path, object],
    ) -> None:
        """``inbox_thread_id == 0`` must yield 422 — Telegram thread_ids are >= 1."""
        client, _, _, _ = app_client
        r = await client.post(
            "/v1/approvals/inbox",
            json={"operator_chat_id": -1001234567890, "inbox_thread_id": 0},
            headers={"Idempotency-Key": new_idempotency_key()},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_post_rejects_negative_inbox_thread_id(
        self,
        app_client: tuple[AsyncClient, Path, Path, object],
    ) -> None:
        """Negative ``inbox_thread_id`` must yield 422."""
        client, _, _, _ = app_client
        r = await client.post(
            "/v1/approvals/inbox",
            json={"operator_chat_id": -1001234567890, "inbox_thread_id": -5},
            headers={"Idempotency-Key": new_idempotency_key()},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /v1/approvals/inbox/{operator_chat_id} tests
# ---------------------------------------------------------------------------


class TestGetInbox:
    @pytest.mark.asyncio
    async def test_get_returns_404_when_no_inbox_open(
        self,
        app_client: tuple[AsyncClient, Path, Path, object],
    ) -> None:
        """No row → 404 with problem+json body."""
        client, _, _, _ = app_client
        r = await client.get("/v1/approvals/inbox/-1001234567890")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_get_returns_row_when_seeded(
        self,
        app_client: tuple[AsyncClient, Path, Path, object],
    ) -> None:
        """Seeded row → 200 with all 4 fields.

        Story 11.3 review P11: registry-api's lifespan engine is read-only,
        so seeding goes through a SHORT-LIVED writable engine that is
        disposed immediately. The write completes (and is fsync'd by
        SQLite WAL) BEFORE the GET fires, so this is not the
        race-with-lifespan-engine pattern P11 warned about — it's the
        same idiom registry-state's writable cache_engine uses. We keep
        a comment so future readers don't reach for ``session_maker``
        (which is intentionally read-only).
        """
        client, db_path, _, _ = app_client
        db_url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_engine(db_url, read_only=False)
        async with engine.begin() as conn:
            await conn.execute(
                ApprovalInbox.__table__.insert(),  # type: ignore[attr-defined]  # SQLAlchemy stubs: Table.__table__ resolves at runtime
                {
                    "operator_chat_id": -1001234567890,
                    "inbox_thread_id": 42,
                    # Story 11.3 review P34: FROZEN_EPOCH.
                    "opened_at": FROZEN_EPOCH,
                    "opened_by_actor_id": "operator-test",
                },
            )
        await engine.dispose()

        r = await client.get("/v1/approvals/inbox/-1001234567890")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["operator_chat_id"] == -1001234567890
        assert body["inbox_thread_id"] == 42
        assert body["opened_by_actor_id"] == "operator-test"
        assert body["opened_at"].startswith(FROZEN_EPOCH.isoformat()[:19])


# ---------------------------------------------------------------------------
# Story 11.3 PP12 — 410 on post-restart cache eviction
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_client_with_state(
    tmp_path: Path,
) -> AsyncGenerator[tuple[AsyncClient, object], None]:
    """Like ``app_client`` but also yields the app for state introspection.

    PP12 needs to clear ``app.state.idempotency_response_cache`` between
    two POSTs to simulate a post-restart cache eviction; this fixture
    exposes the app handle.
    """
    db_path = tmp_path / "state.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    events_dir = tmp_path / "events"
    clock = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)
    app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client, app


@pytest.mark.asyncio
async def test_post_returns_410_when_response_slot_cache_missing_but_idempotency_row_present(
    app_client_with_state: tuple[AsyncClient, object],
) -> None:
    """PP12: post-restart cache eviction → 410 Gone (not 500, not stale 201).

    Simulates the post-restart scenario where the SQLite idempotency row
    is intact (so ``get_or_run`` reports cache-hit → was_run=False) but
    the in-memory ``ResponseSlotCache`` is empty (slot lost on process
    restart). The endpoint must return 410 with a clear "retry with a
    new key" message so the gateway's PP1 retry can recover.
    """
    client, app = app_client_with_state
    body_json = {"operator_chat_id": -1001234567890, "inbox_thread_id": 42}
    key = new_idempotency_key()
    headers = {"Idempotency-Key": key}

    r1 = await client.post("/v1/approvals/inbox", json=body_json, headers=headers)
    assert r1.status_code == 201

    # Simulate the post-restart cache loss: persistent SQLite row stays,
    # in-memory slot cache is cleared.
    app.state.idempotency_response_cache.clear()  # type: ignore[attr-defined]

    r2 = await client.post("/v1/approvals/inbox", json=body_json, headers=headers)
    assert r2.status_code == 410, r2.text
    assert "retry with a new key" in r2.text.lower() or "not recoverable" in r2.text.lower()


# ---------------------------------------------------------------------------
# Story 11.3 PP14 — POST /v1/approvals/inbox requires Tier.TWO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_open_inbox_requires_tier_2(tmp_path: Path) -> None:
    """PP14: ROUTE_TIER_MAP entry enforces tier on POST /v1/approvals/inbox.

    Story 11.3 P35 added ``"POST /v1/approvals/inbox": Tier.TWO`` to
    ``ROUTE_TIER_MAP``. PP14 closes the coverage gap by elevating the
    route to Tier.THREE (above worker's max of TWO) and asserting that a
    worker actor is denied with the canonical 403 + problem+json body
    (mirrors :class:`TestTierEnforcementMiddleware.test_tier_denied_returns_403_problem_json`
    in ``test_middleware.py``).
    """
    from unittest.mock import patch

    from capabilities import Tier

    db_path = tmp_path / "state.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    events_dir = tmp_path / "events"
    clock = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)

    # Elevate POST /v1/approvals/inbox to Tier.THREE (worker max is TWO).
    with patch(
        "registry_api.adapters.middleware.ROUTE_TIER_MAP",
        {"POST /v1/approvals/inbox": Tier.THREE},
    ):
        app = build_app(
            base_dir=events_dir,
            db_url=db_url,
            clock=clock,
            actor_kind="worker",
        )
        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://testserver"
            ) as client,
        ):
            r = await client.post(
                "/v1/approvals/inbox",
                json={"operator_chat_id": -1001234567890, "inbox_thread_id": 42},
                headers={"Idempotency-Key": new_idempotency_key()},
            )
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["type"] == "/errors/forbidden"
    assert body["status"] == 403
    # PP14 also closes Story 11.2.1 DD5 follow-up — a ``capability.denied``
    # signal is emitted via the tier_enforcement_denied log line. The
    # registry-api ``handle_capability_denied`` exception handler logs it
    # (see adapters/errors.py:323); we assert the status-code surface here.
