"""Tests for POST /v1/tasks/{task_id}/decisions (Story 6.4).

Positive tests (AC-11): approve, reject, stop, retry, idempotent replay,
license override dual event emission.
Negative tests (AC-12): 404, 409 state mismatch, 422 validation.

NOTE (Story 8.7.5 PP7): Schema-registry registration is handled by the
repo-root conftest.py session-scoped autouse fixture. If new sibling tests
in this package call unregister_all(), they MUST add a function-scoped
teardown calling ensure_registered() to restore canonical state. See
docs/testing-guide.md "Schema-registry isolation in tests" + Story 7e4ffec.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from random import Random

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from events.ids import new_request_id
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
        op = {"actor_kind": "operator", "actor_id": "test-op", "created_at": now, "updated_at": now}
        task_rows = [
            {"id": _TID_PLAN_READY, "status": "plan_ready", "title": "Plan ready task", **op},
            {"id": _TID_AWAITING, "status": "awaiting_approval", "title": "Awaiting task", **op},
            {"id": _TID_EXECUTING, "status": "executing", "title": "Executing task", **op},
            {"id": _TID_BLOCKED, "status": "blocked", "title": "Blocked task", **op},
            {"id": _TID_COMPLETED, "status": "completed", "title": "Completed task", **op},
            {"id": _TID_FAILED, "status": "failed", "title": "Failed task", **op},
            {"id": _TID_PENDING, "status": "pending", "title": "Pending task", **op},
        ]
        for row in task_rows:
            await conn.execute(Task.__table__.insert(), row)  # type: ignore[attr-defined]  # SQLAlchemy stubs return FromClause; Table.__table__ resolves at runtime
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")  # Story 8.7.6 PP2 — LifespanManager bg state
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
    async def test_license_override_dual_event(
        self,
        app_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
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
        types = [json.loads(line)["type"] for line in lines]
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


# ---------------------------------------------------------------------------
# License gate tests (Story 6.10, AC-1)
# ---------------------------------------------------------------------------


async def _seed_license_flagged_event(db_url: str, task_id: str) -> None:
    """Insert a task.license_flagged event row for the given task."""
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.execute(
            Event.__table__.insert(),  # type: ignore[attr-defined]  # SQLAlchemy stubs return FromClause; Table.__table__ resolves at runtime
            {
                "id": "e-license-flag-000000000000000000",
                "type": "task.license_flagged",
                "schema_version": "1.0.0",
                "emitted_at": FROZEN_EPOCH,
                "emitted_at_monotonic_ns": _FROZEN_MONO_NS,
                "actor_kind": "worker",
                "actor_id": "test-worker",
                "task_id": task_id,
                "session_id": None,
                "parent_event_id": None,
                "request_id": "req-license-flag-test",
                "payload_json": json.dumps(
                    {
                        "task_id": task_id,
                        "reason_code": "copyleft-incompatible",
                        "file_list": ["src/gpl_code.py"],
                        "detected_licenses": ["gpl-2.0"],
                    }
                ),
            },
        )
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")  # Story 8.7.6 PP2 — LifespanManager bg state
async def flagged_client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    """Client with a plan_ready task that has a license_flagged event."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables_with_tasks(db_url)
    await _seed_license_flagged_event(db_url, _TID_PLAN_READY)

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


class TestLicenseGate:
    """Story 6.10 AC-1: approve blocked when license-flagged."""

    @pytest.mark.asyncio
    async def test_approve_blocked_when_flagged(
        self,
        flagged_client: AsyncClient,
    ) -> None:
        """Default approve returns 409 when license_flagged event exists."""
        r = await flagged_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve"},
        )
        assert r.status_code == 409
        body = r.json()
        assert body["type"] == "approval_blocked_by"
        assert body["extensions"]["reason"] == "license_flag"
        assert "license flag" in body["detail"].lower()

    @pytest.mark.asyncio
    async def test_approve_allowed_with_override(
        self,
        flagged_client: AsyncClient,
    ) -> None:
        """Approve with override=license succeeds despite license flag."""
        r = await flagged_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve", "override": "license"},
        )
        assert r.status_code == 202
        assert r.json()["action"] == "approve"

    @pytest.mark.asyncio
    async def test_approve_allowed_when_no_flag(
        self,
        app_client: AsyncClient,
    ) -> None:
        """Approve succeeds normally when no license_flagged event exists."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve"},
        )
        assert r.status_code == 202

    @pytest.mark.asyncio
    async def test_reject_not_blocked_by_license_flag(
        self,
        flagged_client: AsyncClient,
    ) -> None:
        """Reject is not blocked by license flag (only approve is gated)."""
        r = await flagged_client.post(
            f"/v1/tasks/{_TID_AWAITING}/decisions",
            json={"action": "reject", "reason": "bad license"},
        )
        assert r.status_code == 202

    @pytest.mark.asyncio
    async def test_license_gate_does_not_burn_idempotency_slot(
        self,
        flagged_client: AsyncClient,
    ) -> None:
        """A blocked approve doesn't consume an idempotency slot."""
        idem_key = new_request_id(clock=_FROZEN_CLOCK, rng=Random(42))
        headers = {"Idempotency-Key": idem_key}

        # First request: blocked by license gate
        r1 = await flagged_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve"},
            headers=headers,
        )
        assert r1.status_code == 409

        # Second request with override: succeeds (not replayed)
        r2 = await flagged_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve", "override": "license"},
            headers=headers,
        )
        assert r2.status_code == 202
        assert r2.json()["idempotency_status"] == "applied"

    @pytest.mark.asyncio
    async def test_override_on_non_approve_rejected(
        self,
        app_client: AsyncClient,
    ) -> None:
        """override=license on a reject action is rejected by Pydantic validation."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_AWAITING}/decisions",
            json={"action": "reject", "override": "license"},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Budget gate tests (Story 6.11, AC-2)
# ---------------------------------------------------------------------------


async def _seed_budget_exceeded_event(db_url: str, task_id: str) -> None:
    """Insert a task.budget_exceeded event row and transition task to blocked."""
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.execute(
            Event.__table__.insert(),  # type: ignore[attr-defined]  # SQLAlchemy stubs return FromClause; Table.__table__ resolves at runtime
            {
                "id": "e-budget-exceeded-0000000000000000",
                "type": "task.budget_exceeded",
                "schema_version": "1.0.0",
                "emitted_at": FROZEN_EPOCH,
                "emitted_at_monotonic_ns": _FROZEN_MONO_NS,
                "actor_kind": "worker",
                "actor_id": "test-worker",
                "task_id": task_id,
                "session_id": None,
                "parent_event_id": None,
                "request_id": "req-budget-test",
                "payload_json": json.dumps(
                    {
                        "task_id": task_id,
                        "token_limit": 50_000,
                        "tokens_used": 52_340,
                        "step": 3,
                    }
                ),
            },
        )
        from sqlalchemy import update as sa_update

        await conn.execute(
            sa_update(Task)
            .where(Task.id == task_id)
            .values(
                status="blocked",
                blocker_reason="budget_exceeded",
            )
        )
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")  # Story 8.7.6 PP2 — LifespanManager bg state
async def budget_blocked_client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    """Client with a plan_ready task that has a budget_exceeded event."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables_with_tasks(db_url)
    await _seed_budget_exceeded_event(db_url, _TID_PLAN_READY)

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


class TestBudgetGate:
    """Story 6.11 AC-2: approve blocked when budget exceeded."""

    @pytest.mark.asyncio
    async def test_approve_blocked_when_budget_exceeded(
        self,
        budget_blocked_client: AsyncClient,
    ) -> None:
        """Default approve returns 409 when budget_exceeded event exists."""
        r = await budget_blocked_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve"},
        )
        assert r.status_code == 409
        body = r.json()
        assert body["type"] == "approval_blocked_by"
        assert body["extensions"]["reason"] == "budget_exceeded"
        assert "budget" in body["detail"].lower()

    @pytest.mark.asyncio
    async def test_approve_allowed_with_budget_override(
        self,
        budget_blocked_client: AsyncClient,
    ) -> None:
        """Approve with override=budget succeeds despite budget exceeded."""
        r = await budget_blocked_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve", "override": "budget"},
        )
        assert r.status_code == 202
        assert r.json()["action"] == "approve"

    @pytest.mark.asyncio
    async def test_approve_allowed_when_no_budget_event(
        self,
        app_client: AsyncClient,
    ) -> None:
        """Approve succeeds normally when no budget_exceeded event exists."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve"},
        )
        assert r.status_code == 202

    @pytest.mark.asyncio
    async def test_budget_gate_does_not_burn_idempotency_slot(
        self,
        budget_blocked_client: AsyncClient,
    ) -> None:
        """A blocked approve doesn't consume an idempotency slot."""
        idem_key = new_request_id(clock=_FROZEN_CLOCK, rng=Random(99))
        headers = {"Idempotency-Key": idem_key}

        r1 = await budget_blocked_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve"},
            headers=headers,
        )
        assert r1.status_code == 409

        r2 = await budget_blocked_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve", "override": "budget"},
            headers=headers,
        )
        assert r2.status_code == 202
        assert r2.json()["idempotency_status"] == "applied"

    @pytest.mark.asyncio
    async def test_override_budget_on_non_approve_rejected(
        self,
        app_client: AsyncClient,
    ) -> None:
        """override=budget on a reject action is rejected by Pydantic validation."""
        r = await app_client.post(
            f"/v1/tasks/{_TID_AWAITING}/decisions",
            json={"action": "reject", "override": "budget"},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Dual-gate tests (both license + budget flags, code-review gap fix)
# ---------------------------------------------------------------------------


async def _seed_dual_flags(db_url: str, task_id: str) -> None:
    """Insert both license_flagged and budget_exceeded events, set blocked."""
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.execute(
            Event.__table__.insert(),  # type: ignore[attr-defined]  # SQLAlchemy stubs return FromClause; Table.__table__ resolves at runtime
            {
                "id": "e-dual-license-00000000000000000",
                "type": "task.license_flagged",
                "schema_version": "1.0.0",
                "emitted_at": FROZEN_EPOCH,
                "emitted_at_monotonic_ns": _FROZEN_MONO_NS,
                "actor_kind": "worker",
                "actor_id": "test-worker",
                "task_id": task_id,
                "session_id": None,
                "parent_event_id": None,
                "request_id": "req-dual-test",
                "payload_json": json.dumps(
                    {
                        "task_id": task_id,
                        "reason_code": "copyleft-incompatible",
                        "file_list": ["src/gpl_code.py"],
                        "detected_licenses": ["gpl-2.0"],
                    }
                ),
            },
        )
        await conn.execute(
            Event.__table__.insert(),  # type: ignore[attr-defined]  # SQLAlchemy stubs return FromClause; Table.__table__ resolves at runtime
            {
                "id": "e-dual-budget-00000000000000000",
                "type": "task.budget_exceeded",
                "schema_version": "1.0.0",
                "emitted_at": FROZEN_EPOCH,
                "emitted_at_monotonic_ns": _FROZEN_MONO_NS + 1,
                "actor_kind": "worker",
                "actor_id": "test-worker",
                "task_id": task_id,
                "session_id": None,
                "parent_event_id": None,
                "request_id": "req-dual-budget",
                "payload_json": json.dumps(
                    {
                        "task_id": task_id,
                        "token_limit": 50_000,
                        "tokens_used": 52_340,
                        "step": 3,
                    }
                ),
            },
        )
        from sqlalchemy import update as sa_update

        await conn.execute(
            sa_update(Task)
            .where(Task.id == task_id)
            .values(
                status="blocked",
                blocker_reason="budget_exceeded",
            )
        )
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")  # Story 8.7.6 PP2 — LifespanManager bg state
async def dual_blocked_client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    """Client with a task that has BOTH license and budget flags."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables_with_tasks(db_url)
    await _seed_dual_flags(db_url, _TID_PLAN_READY)

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


class TestDualGate:
    """Both license_flagged and budget_exceeded — gate ordering and override interaction."""

    @pytest.mark.asyncio
    async def test_approve_blocked_by_license_gate_first(
        self,
        dual_blocked_client: AsyncClient,
    ) -> None:
        """Default approve returns 409 from the license gate (checked first)."""
        r = await dual_blocked_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve"},
        )
        assert r.status_code == 409
        body = r.json()
        assert body["type"] == "approval_blocked_by"
        assert body["extensions"]["reason"] == "license_flag"

    @pytest.mark.asyncio
    async def test_override_budget_still_hits_license_gate(
        self,
        dual_blocked_client: AsyncClient,
    ) -> None:
        """override=budget skips budget gate but license gate still blocks."""
        r = await dual_blocked_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve", "override": "budget"},
        )
        assert r.status_code == 409
        body = r.json()
        assert body["extensions"]["reason"] == "license_flag"
