"""Story 6.14 — Tier-3 negative test (FR38 / NFR-S6).

Deterministic integration tests that verify the Tier-3 capability enforcement
in the ``TierEnforcementMiddleware``. The harness mirrors the
``test_license_scan.py`` pattern: in-process ASGI app via
``httpx.ASGITransport`` + SQLite + frozen clock. No Docker needed.

Test seam: the harness builds the ASGI app with ``actor_kind="worker"``
(max tier TWO) and patches ``ROUTE_TIER_MAP`` to elevate a route to
``Tier.THREE``, triggering the denial path.

Test inventory:

  - ``test_tier3_denied_without_approval``
        Patch route to Tier.THREE, POST with worker actor (max tier TWO)
        → assert 403 with RFC 7807 ``/errors/forbidden``.
  - ``test_tier2_succeeds_with_worker``
        Worker (max tier TWO) on Tier-2 route → assert 202 success.
        Positive control: proves denial was tier-specific, not a general
        middleware failure.
  - ``test_operator_denied_tier3_without_approval``
        Operator (max tier THREE) on Tier-3 route → assert 403 with
        approval-gate reason. Proves the denial reason differs between
        worker (actor-kind gate) and operator (approval gate).
  - ``test_read_methods_bypass_tier_enforcement``
        GET request with worker actor, even on Tier-3 route
        → assert 200 (read-only methods skip enforcement).

References:
  - prd.md:868  FR38 (Tier-3 requires explicit approval)
  - prd.md:926  NFR-S6 (negative-test regression-proof)
  - middleware.py  TierEnforcementMiddleware + ROUTE_TIER_MAP
  - tiers.py  check_tier() + _MAX_TIER_BY_ACTOR
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from random import Random
from types import MappingProxyType
from unittest.mock import patch

import httpx
import pytest
from asgi_lifespan import LifespanManager
from capabilities.tiers import Tier
from events import (
    FROZEN_EPOCH,
    FrozenClock,
)
from events.ids import (
    new_idempotency_key,
    new_request_id,
    new_task_id,
)
from events.payloads import (
    ApprovalGrantedPayload,
    ApprovalRejectedPayload,
    BudgetOverridePayload,
    LicenseOverridePayload,
    TaskLicenseFlaggedPayload,
    TaskRetryRequestedPayload,
    TaskStopRequestedPayload,
    Tier3ActionAttemptedPayload,
)
from events.schema_registry import register as _register_event
from registry_api.app import build_app
from registry_state.adapters.event_log import current_day_path
from registry_state.adapters.sqlite_store import create_engine as _create_engine
from registry_state.schema import Base, Task
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.integration._aiosqlite_teardown import (
    current_event_loop_or_none,
    drain_aiosqlite_workers_before_loop_close,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FROZEN_MONO_NS = 1_000_000
_INITIAL_STATUS = "plan_ready"

# Route tier map patches for testing.
_TIER3_ROUTE_MAP = MappingProxyType(
    {
        "POST /v1/tasks": Tier.THREE,
    }
)
_TIER2_ROUTE_MAP = MappingProxyType(
    {
        "POST /v1/tasks": Tier.TWO,
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _seed_tables(db_url: str) -> None:
    engine = _create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _seed_task(
    session_maker,
    clock: FrozenClock,
    status: str = _INITIAL_STATUS,
) -> str:
    """Insert a task row and return its ID."""
    task_id = new_task_id(clock=clock)
    now = clock.now()
    async with session_maker() as session:
        task = Task(
            id=task_id,
            status=status,
            created_at=now,
            updated_at=now,
            actor_kind="operator",
            actor_id="test-operator",
            title="tier3-negative-test-task",
        )
        session.add(task)
        await session.commit()
    return task_id


class _Harness:
    """Container for ASGI + HTTP client wiring.

    Owns its own asyncio event loop so synchronous test functions can
    drive async setup/teardown via ``loop.run_until_complete``.
    """

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.lifespan: LifespanManager | None = None
        self.client: httpx.AsyncClient | None = None
        self.writable_session: async_sessionmaker | None = None
        self.writable_engine: object | None = None
        self.clock: FrozenClock | None = None
        self.events_dir: Path | None = None
        self._prev_loop: asyncio.AbstractEventLoop | None = None


def _assert_ready(h: _Harness) -> None:
    """Assert harness attributes are set, narrowing types for mypy."""
    assert h.loop is not None
    assert h.client is not None
    assert h.writable_session is not None
    assert h.clock is not None
    assert h.events_dir is not None


def _build_harness(tmp_path: Path, actor_kind: str) -> _Harness:
    """Build a harness with the given actor_kind for tier enforcement."""
    h = _Harness()
    prev_loop = current_event_loop_or_none()
    loop = asyncio.new_event_loop()
    h.loop = loop
    asyncio.set_event_loop(loop)

    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    writable_engine = _create_engine(db_url)
    h.writable_engine = writable_engine
    loop.run_until_complete(_seed_tables(db_url))
    h.writable_session = async_sessionmaker(writable_engine, expire_on_commit=False)

    clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
    h.clock = clock

    events_dir = tmp_path / "events"
    h.events_dir = events_dir

    async def _setup() -> tuple[LifespanManager, httpx.AsyncClient]:
        app = build_app(
            base_dir=events_dir,
            db_url=db_url,
            clock=clock,
            actor_kind=actor_kind,
            idempotency_db_url=_db_url(tmp_path / "idempotency.sqlite3"),
            create_idempotency_schema_on_start=True,
        )
        mgr = LifespanManager(app, startup_timeout=30, shutdown_timeout=30)
        await mgr.__aenter__()
        transport = httpx.ASGITransport(app=mgr.app)
        client = httpx.AsyncClient(
            transport=transport,
            base_url="http://registry-api:8080",
        )
        return mgr, client

    h.lifespan, h.client = loop.run_until_complete(_setup())

    # Store prev_loop for teardown
    h._prev_loop = prev_loop
    return h


def _teardown_harness(h: _Harness) -> None:
    """Teardown harness resources."""
    assert h.loop is not None
    prev_loop = getattr(h, "_prev_loop", None)

    async def _teardown() -> None:
        if h.client is not None:
            await h.client.aclose()
        if h.lifespan is not None:
            await h.lifespan.__aexit__(None, None, None)
        if h.writable_engine is not None:
            await h.writable_engine.dispose()

    h.loop.run_until_complete(_teardown())
    drain_aiosqlite_workers_before_loop_close(h.loop)
    asyncio.set_event_loop(prev_loop)
    h.loop.close()


@pytest.fixture
def worker_harness(tmp_path) -> _Harness:
    """Harness with worker actor_kind (max tier TWO) for denial tests."""
    h = _build_harness(tmp_path, actor_kind="worker")
    try:
        yield h
    finally:
        _teardown_harness(h)


@pytest.fixture
def operator_harness(tmp_path) -> _Harness:
    """Harness with operator actor_kind (max tier THREE) for approval-gate tests."""
    h = _build_harness(tmp_path, actor_kind="operator")
    try:
        yield h
    finally:
        _teardown_harness(h)


# ---------------------------------------------------------------------------
# Schema-registry guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    """Re-register required event types for every test."""
    _payloads = [
        ("approval.granted", "1.0.0", ApprovalGrantedPayload),
        ("approval.rejected", "1.0.0", ApprovalRejectedPayload),
        ("task.stop_requested", "1.0.0", TaskStopRequestedPayload),
        ("task.retry_requested", "1.0.0", TaskRetryRequestedPayload),
        ("task.license_flagged", "1.0.0", TaskLicenseFlaggedPayload),
        ("tier3.license_override", "1.0.0", LicenseOverridePayload),
        ("tier3.budget_override", "1.0.0", BudgetOverridePayload),
        ("tier3.action_attempted", "1.0.0", Tier3ActionAttemptedPayload),
    ]
    for type_name, version, payload_cls in _payloads:
        with contextlib.suppress(KeyError, ValueError):
            _register_event(type_name, version, payload_cls)


# ---------------------------------------------------------------------------
# Event-log helpers
# ---------------------------------------------------------------------------


async def _count_jsonl_events(events_dir: Path, task_id: str) -> int:
    """Count JSONL event log lines for a given task.

    The materializer is not running in the harness, so we read the
    JSONL event log directly instead of querying the SQL Event table.
    """
    log_path = current_day_path(events_dir, FROZEN_EPOCH)
    if not log_path.exists():
        return 0
    count = 0
    for line in log_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            payload = obj.get("payload", {})
            if isinstance(payload, dict) and payload.get("task_id") == task_id:
                count += 1
        except (json.JSONDecodeError, KeyError):
            continue
    return count


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------


async def _submit_decision(
    client: httpx.AsyncClient,
    task_id: str,
    action: str,
    idempotency_key: str,
    rng: Random,
    override: str | None = None,
) -> httpx.Response:
    """POST a decision to the endpoint and return the response."""
    body: dict = {"action": action}
    if override is not None:
        body["override"] = override
    headers = {
        "Idempotency-Key": idempotency_key,
        "X-Request-ID": new_request_id(rng=rng),
        "X-Actor-ID": "test-actor",
    }
    return await client.post(
        f"/v1/tasks/{task_id}/decisions",
        json=body,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Test: Tier-3 denied without approval (AC: #1, #2)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_tier3_denied_without_approval(worker_harness: _Harness) -> None:
    """Worker (max tier TWO) POST to Tier-3 route → 403 RFC 7807."""
    h = worker_harness
    _assert_ready(h)

    rng = Random(42)

    async def _run() -> tuple[httpx.Response, str]:
        task_id = await _seed_task(h.writable_session, h.clock)
        key = new_idempotency_key(rng=rng)
        with patch("registry_api.adapters.middleware.ROUTE_TIER_MAP", _TIER3_ROUTE_MAP):
            resp = await _submit_decision(h.client, task_id, "approve", key, rng)
            return resp, task_id

    resp, task_id = h.loop.run_until_complete(_run())

    assert resp.status_code == 403, (
        f"expected 403 (tier denied), got {resp.status_code}: {resp.text}"
    )
    assert "application/problem+json" in resp.headers.get("content-type", ""), (
        "expected RFC 7807 content-type application/problem+json"
    )
    body = resp.json()
    assert body.get("type") == "/errors/forbidden", (
        f"expected type='/errors/forbidden', got {body.get('type')}"
    )
    assert body.get("status") == 403, f"expected status=403, got {body.get('status')}"
    assert body.get("title") is not None, "expected RFC 7807 'title' field"
    assert body.get("detail") is not None, "expected RFC 7807 'detail' field with denial reason"

    # AC #2: assert no side-effects — no events written to JSONL log.
    event_count = h.loop.run_until_complete(_count_jsonl_events(h.events_dir, task_id))
    assert event_count == 0, f"expected 0 events after tier denial, got {event_count}"


# ---------------------------------------------------------------------------
# Test: Tier-3 succeeds with operator (AC: #3)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_tier2_succeeds_with_worker(worker_harness: _Harness) -> None:
    """Worker (max tier TWO) POST to Tier-2 route → 202 success.

    Positive control: proves the denial in test_tier3_denied_without_approval
    was specifically due to Tier-3, not a general middleware failure.
    """
    h = worker_harness
    _assert_ready(h)

    rng = Random(42)

    async def _run() -> httpx.Response:
        task_id = await _seed_task(h.writable_session, h.clock)
        key = new_idempotency_key(rng=rng)
        with patch("registry_api.adapters.middleware.ROUTE_TIER_MAP", _TIER2_ROUTE_MAP):
            return await _submit_decision(h.client, task_id, "approve", key, rng)

    resp = h.loop.run_until_complete(_run())

    assert resp.status_code == 202, (
        f"expected 202 (worker passes Tier-2), got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body.get("action") == "approve", (
        f"expected action='approve' in response body, got {body.get('action')}"
    )


# ---------------------------------------------------------------------------
# Test: operator denied Tier-3 via approval gate (AC: #3 extended)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_operator_denied_tier3_without_approval(operator_harness: _Harness) -> None:
    """Operator (max tier THREE) POST to Tier-3 route → 403 approval-gate denial.

    The middleware never passes ``has_approval=True`` to ``check_tier()``, so
    even the operator is denied Tier-3 routes. The denial reason differs
    from the worker case: worker hits the actor-kind gate
    (``"actor_kind 'worker' allows Tier.2 at most"``), while the operator
    hits the approval gate (``"no_matching_approval"``).
    """
    h = operator_harness
    _assert_ready(h)

    rng = Random(42)

    async def _run() -> httpx.Response:
        task_id = await _seed_task(h.writable_session, h.clock)
        key = new_idempotency_key(rng=rng)
        with patch("registry_api.adapters.middleware.ROUTE_TIER_MAP", _TIER3_ROUTE_MAP):
            return await _submit_decision(h.client, task_id, "approve", key, rng)

    resp = h.loop.run_until_complete(_run())

    assert resp.status_code == 403, (
        f"expected 403 (operator denied Tier-3 approval gate), got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body.get("type") == "/errors/forbidden", (
        f"expected type='/errors/forbidden', got {body.get('type')}"
    )
    # Operator passes the actor-kind gate (max_tier=THREE) but fails
    # the approval gate — verify the reason mentions "approval".
    detail = body.get("detail", "")
    assert "approval" in detail.lower(), f"expected approval-gate denial reason, got: {detail}"


# ---------------------------------------------------------------------------
# Test: read methods bypass tier enforcement (defense test)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_read_methods_bypass_tier_enforcement(worker_harness: _Harness) -> None:
    """GET request with worker actor, even on Tier-3 route → 200."""
    h = worker_harness
    _assert_ready(h)

    async def _run() -> tuple[httpx.Response, str]:
        # Seed a task so GET /v1/tasks/{id} returns 200 instead of 404.
        task_id = await _seed_task(h.writable_session, h.clock)
        with patch("registry_api.adapters.middleware.ROUTE_TIER_MAP", _TIER3_ROUTE_MAP):
            return await h.client.get(f"/v1/tasks/{task_id}"), task_id

    resp, task_id = h.loop.run_until_complete(_run())

    assert resp.status_code == 200, (
        f"expected 200 (GET bypasses tier enforcement), got {resp.status_code}: {resp.text}"
    )
