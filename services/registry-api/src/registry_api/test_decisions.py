"""Tests for POST /v1/tasks/{task_id}/decisions (Story 6.4).

Positive tests (AC-11): approve, reject, stop, retry, idempotent replay,
license override dual event emission.
Negative tests (AC-12): 404, 409 state mismatch, 422 validation.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from random import Random

from events.ids import new_idempotency_key, new_request_id
from httpx import ASGITransport, AsyncClient
from registry_state.adapters.sqlite_store import create_engine  # noqa: IMP001
from registry_state.schema import Base, Event, Task  # noqa: IMP001
from sqlalchemy import select

from registry_api.app import build_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FROZEN_MONO_NS = 1_000_000
_FROZEN_CLOCK = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)

# Fixed task IDs used across tests.
_TID_PLAN_READY = "t-00000000-0000-7000-8000-000000000001"
_TID_AWAITING = "t-00000000-0000-7000-8000-000000000002"
_TID_EXECUTING = "t-00000000-0000-7000-8000-000000000003"
_TID_BLOCKED = "t-00000000-0000-7000-8000-000000000004"
_TID_COMPLETED = "t-00000000-0000-7000-8000-000000000005"
_TID_FAILED = "t-00000000-0000-7000-8000-000000000006"
_TID_PENDING = "t-00000000-0000-7000-8000-000000000007"
_TID_NONEXISTENT = "t-00000000-0000-7000-8000-999999999999"


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _seed_tables_with_tasks(db_url: str) -> None:
    """Create ORM tables and seed tasks in various statuses."""
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        now = datetime(2026, 1, 1, tzinfo=UTC)
        tasks = [
            Task(id=_TID_PLAN_READY, status="plan_ready", created_at=now, updated_at=now, actor_kind="operator", actor_id="test-op", title="Plan ready task"),
            Task(id=_TID_AWAITING, status="awaiting_approval", created_at=now, updated_at=now, actor_kind="operator", actor_id="test-op", title="Awaiting task"),
            Task(id=_TID_EXECUTING, status="executing", created_at=now, updated_at=now, actor_kind="operator", actor_id="test-op", title="Executing task"),
            Task(id=_TID_BLOCKED, status="blocked", created_at=now, updated_at=now, actor_kind="operator", actor_id="test-op", title="Blocked task"),
            Task(id=_TID_COMPLETED, status="completed", created_at=now, updated_at=now, actor_kind="operator", actor_id="test-op", title="Completed task"),
            Task(id=_TID_FAILED, status="failed", created_at=now, updated_at=now, actor_kind="operator", actor_id="test-op", title="Failed task"),
            Task(id=_TID_PENDING, status="pending", created_at=now, updated_at=now, actor_kind="operator", actor_id="test-op", title="Pending task"),
        ]
        for t in tasks:
            await conn.execute(
                Task.__table__.insert(),
                {
                    "id": t.id,
                    "status": t.status,
                    "created_at": t.created_at,
                    "updated_at": t.updated_at,
                    "actor_kind": t.actor_kind,
                    "actor_id": t.actor_id,
                    "title": t.title,
                },
            )
    await engine.dispose()


@pytest_asyncio.fixture
async def app_client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables_with_tasks(db_url)
    events_dir = tmp_path / "events"
    clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
    app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client


# ---------------------------------------------------------------------------
# Positive tests (AC-11)
# ---------------------------------------------------------------------------


class TestDecisionsPositive:
    @pytest.mark.asyncio
    async def test_approve_on_plan_ready(self, app_client: AsyncClient) -> None:
        """AC-11: approve on plan_ready → 202, approval.granted emitted."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve"},
        )
        assert r.status_code == 202
        body = r.json()
        assert body["task_id"] == _TID_PLAN_READY
        assert body["action"] == "approve"
        assert body["decision_id"].startswith("d-")
        assert body["idempotency_status"] == "applied"
        assert "decided_at" in body
        assert r.headers["x-idempotency-status"] == "applied"

    @pytest.mark.asyncio
    async def test_reject_on_awaiting_approval(self, app_client: AsyncClient) -> None:
        """AC-11: reject with reason on awaiting_approval → 202."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_AWAITING}/decisions",
            json={"action": "reject", "reason": "Bad plan"},
        )
        assert r.status_code == 202
        body = r.json()
        assert body["action"] == "reject"
        assert body["task_id"] == _TID_AWAITING

    @pytest.mark.asyncio
    async def test_stop_on_executing(self, app_client: AsyncClient) -> None:
        """AC-11: stop on executing → 200."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_EXECUTING}/decisions",
            json={"action": "stop"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["action"] == "stop"

    @pytest.mark.asyncio
    async def test_retry_with_hint_on_blocked(self, app_client: AsyncClient) -> None:
        """AC-11: retry with hint on blocked → 200."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_BLOCKED}/decisions",
            json={"action": "retry", "hint": "Focus on rate limiting"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["action"] == "retry"
        assert body["task_id"] == _TID_BLOCKED

    @pytest.mark.asyncio
    async def test_idempotent_replay(self, app_client: AsyncClient) -> None:
        """AC-11: second identical request returns replayed response."""
        idem_key = new_request_id(clock=_FROZEN_CLOCK, rng=Random(2014))
        headers = {"Idempotency-Key": idem_key}

        r1 = await app_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve"},
            headers=headers,
        )
        assert r1.status_code == 202
        assert r1.json()["idempotency_status"] == "applied"

        r2 = await app_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve"},
            headers=headers,
        )
        assert r2.status_code == 202
        body2 = r2.json()
        assert body2["idempotency_status"] == "replayed"
        assert body2["decision_id"] == r1.json()["decision_id"]
        assert r2.headers["x-idempotency-status"] == "replayed"

    @pytest.mark.asyncio
    async def test_license_override_dual_event(self, app_client: AsyncClient, tmp_path: Path) -> None:
        """AC-8/AC-11: approve with license override emits both events."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_AWAITING}/decisions",
            json={"action": "approve", "override": "license"},
        )
        assert r.status_code == 202
        body = r.json()
        assert body["action"] == "approve"

        # Verify two events were written to the event log.
        events_dir = tmp_path / "events"
        event_files = list(events_dir.glob("*.jsonl"))
        assert len(event_files) >= 1
        lines: list[str] = []
        for f in event_files:
            lines.extend(f.read_text().splitlines())
        assert len(lines) == 2
        import json

        types = [json.loads(l)["type"] for l in lines]
        assert "approval.granted" in types
        assert "tier3.license_override" in types

    @pytest.mark.asyncio
    async def test_approve_on_awaiting_approval(self, app_client: AsyncClient) -> None:
        """approve also works on awaiting_approval status."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_AWAITING}/decisions",
            json={"action": "approve"},
        )
        assert r.status_code == 202

    @pytest.mark.asyncio
    async def test_retry_on_failed(self, app_client: AsyncClient) -> None:
        """retry on failed status → 200."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_FAILED}/decisions",
            json={"action": "retry"},
        )
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_stop_on_plan_ready(self, app_client: AsyncClient) -> None:
        """stop on plan_ready → 200 (non-terminal state)."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "stop"},
        )
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Negative tests (AC-12)
# ---------------------------------------------------------------------------


class TestDecisionsNegative:
    @pytest.mark.asyncio
    async def test_404_nonexistent_task(self, app_client: AsyncClient) -> None:
        """AC-12: decision on nonexistent task → 404."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_NONEXISTENT}/decisions",
            json={"action": "approve"},
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_409_approve_on_completed(self, app_client: AsyncClient) -> None:
        """AC-12: approve on completed task → 409."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_COMPLETED}/decisions",
            json={"action": "approve"},
        )
        assert r.status_code == 409
        assert "not allowed" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_409_retry_on_pending(self, app_client: AsyncClient) -> None:
        """AC-12: retry on pending task → 409."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_PENDING}/decisions",
            json={"action": "retry"},
        )
        assert r.status_code == 409
        assert "not allowed" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_422_invalid_action(self, app_client: AsyncClient) -> None:
        """AC-12: invalid action value → 422."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "explode"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_422_override_on_non_approve(self, app_client: AsyncClient) -> None:
        """AC-12: override on reject action → 422."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_AWAITING}/decisions",
            json={"action": "reject", "override": "license"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_409_stop_on_completed(self, app_client: AsyncClient) -> None:
        """stop on terminal state (completed) → 409."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_COMPLETED}/decisions",
            json={"action": "stop"},
        )
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_409_approve_on_executing(self, app_client: AsyncClient) -> None:
        """approve on executing task → 409 (not plan_ready or awaiting_approval)."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_EXECUTING}/decisions",
            json={"action": "approve"},
        )
        assert r.status_code == 409
