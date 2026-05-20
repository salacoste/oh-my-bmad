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
from datetime import UTC, datetime
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
from registry_state.domain.event_types import (  # noqa: IMP001 — test fixture re-registers canonical event types after sibling tests' unregister_all() (Epic 8 retro debt #3)
    ensure_registered,
)
from registry_state.schema import (  # noqa: IMP001 — test fixture imports ORM models for Base.metadata.create_all seeding
    ApprovalInbox,
    Base,
)

from registry_api.app import build_app


@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    """Re-register canonical event types before every test.

    Same rationale as ``test_decisions.py``: sibling tests'
    ``unregister_all()`` autouse fixtures wipe the global registry; module-
    level ``register()`` calls in ``event_types.py`` only run ONCE per
    process. We call ``ensure_registered()`` per-test to restore the
    canonical set including Story 11.3's ``approval.inbox_opened``.
    """
    ensure_registered()


@pytest_asyncio.fixture
async def app_client(tmp_path: Path) -> AsyncGenerator[tuple[AsyncClient, Path, Path], None]:
    """Build an in-process registry-api with a fresh SQLite + JSONL store.

    Yields ``(client, db_path, events_dir)`` so tests can directly seed
    the ``approval_inbox`` table (for GET tests) and inspect the JSONL
    log (for POST tests) without going through the materializer.
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
        yield client, db_path, events_dir


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
        app_client: tuple[AsyncClient, Path, Path],
    ) -> None:
        """First POST → 201 with applied status; JSONL has exactly one envelope."""
        client, _, events_dir = app_client
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
        app_client: tuple[AsyncClient, Path, Path],
    ) -> None:
        """Same Idempotency-Key → 201 with X-Idempotency-Status: replayed; only one event."""
        client, _, events_dir = app_client
        body_json = {"operator_chat_id": -1001234567890, "inbox_thread_id": 42}
        key = new_idempotency_key()
        headers = {"Idempotency-Key": key}

        r1 = await client.post("/v1/approvals/inbox", json=body_json, headers=headers)
        r2 = await client.post("/v1/approvals/inbox", json=body_json, headers=headers)

        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.headers["x-idempotency-status"] == "applied"
        assert r2.headers["x-idempotency-status"] == "replayed"
        # Byte-identical body on replay (cache hit returns the original).
        assert r1.content == r2.content

        envelopes = _read_jsonl_envelopes(events_dir)
        assert len(envelopes) == 1, "Idempotency dedup must not append a second envelope"

    @pytest.mark.asyncio
    async def test_post_rejects_zero_inbox_thread_id(
        self,
        app_client: tuple[AsyncClient, Path, Path],
    ) -> None:
        """``inbox_thread_id == 0`` must yield 422 — Telegram thread_ids are >= 1."""
        client, _, _ = app_client
        r = await client.post(
            "/v1/approvals/inbox",
            json={"operator_chat_id": -1001234567890, "inbox_thread_id": 0},
            headers={"Idempotency-Key": new_idempotency_key()},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_post_rejects_negative_inbox_thread_id(
        self,
        app_client: tuple[AsyncClient, Path, Path],
    ) -> None:
        """Negative ``inbox_thread_id`` must yield 422."""
        client, _, _ = app_client
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
        app_client: tuple[AsyncClient, Path, Path],
    ) -> None:
        """No row → 404 with problem+json body."""
        client, _, _ = app_client
        r = await client.get("/v1/approvals/inbox/-1001234567890")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_get_returns_row_when_seeded(
        self,
        app_client: tuple[AsyncClient, Path, Path],
    ) -> None:
        """Seeded row → 200 with all 4 fields."""
        client, db_path, _ = app_client

        # Seed the approval_inbox row directly (bypassing the materializer
        # so the test is hermetic — the materializer is exercised separately
        # in test_handlers.py).
        db_url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_engine(db_url)
        async with engine.begin() as conn:
            await conn.execute(
                ApprovalInbox.__table__.insert(),  # type: ignore[attr-defined]  # SQLAlchemy stubs: Table.__table__ resolves at runtime
                {
                    "operator_chat_id": -1001234567890,
                    "inbox_thread_id": 42,
                    "opened_at": datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC),
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
        assert body["opened_at"].startswith("2026-05-20T12:00:00")
