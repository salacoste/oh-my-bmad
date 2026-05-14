"""Story 6.13 — License gate integration test (FR40 / FR41 / NFR-S8).

Deterministic integration tests that verify the license-flag gate in the
``POST /v1/tasks/{id}/decisions`` endpoint. The harness mirrors the
``test_decision_interleaving.py`` pattern: in-process ASGI app via
``httpx.ASGITransport`` + SQLite + frozen clock. No Docker needed.

Critical detail: ``_check_license_gate()`` queries the SQL ``Event`` table
(not the JSONL log). The materializer is not running, so the test inserts
the ``task.license_flagged`` event directly into the SQL ``Event`` table to
simulate what the materializer would have done.

Test inventory:

  - ``test_license_flag_blocks_approval``
        Seed task + license_flagged event in SQL, POST /approve without
        override → assert 409 with approval_blocked_by / license_flag.
  - ``test_license_override_approve``
        Seed task + license_flagged event, POST /approve --override license
        → assert 202 + both approval.granted and tier3.license_override events.
  - ``test_approve_without_license_flag``
        Seed task without license flag, POST /approve → assert 202 + single
        approval.granted event. Happy-path baseline.
  - ``test_license_flag_does_not_block_reject_or_stop``
        Seed task + license_flagged event, POST /reject and /stop → assert
        both succeed (license gate only blocks approve).

References:
  - prd.md:870  FR40 (license-scan blocks approval gate)
  - prd.md:871  FR41 (operator override with /approve --override license)
  - prd.md:928  NFR-S8 (license contamination prevention)
  - decisions.py:103-128  _check_license_gate (queries SQL Event table)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from random import Random

import httpx
import pytest
from asgi_lifespan import LifespanManager
from events import (
    FROZEN_EPOCH,
    FrozenClock,
)
from events.ids import (
    new_event_id,
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
)
from events.schema_registry import register as _register_event
from registry_api.app import build_app
from registry_state.adapters.event_log import current_day_path
from registry_state.adapters.sqlite_store import create_engine as _create_engine
from registry_state.schema import Base, Event, Task
from sqlalchemy.ext.asyncio import async_sessionmaker

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FROZEN_MONO_NS = 1_000_000
_INITIAL_STATUS = "plan_ready"


# ---------------------------------------------------------------------------
# Harness — in-process ASGI app + SQLite + frozen clock
# ---------------------------------------------------------------------------


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _seed_tables(db_url: str) -> None:
    engine = _create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


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


def _assert_ready(h: _Harness) -> None:
    """Assert harness attributes are set, narrowing types for mypy."""
    assert h.loop is not None
    assert h.client is not None
    assert h.writable_session is not None
    assert h.clock is not None


@pytest.fixture
def harness(tmp_path) -> _Harness:
    """Sync fixture that owns its own event loop."""
    h = _Harness()
    try:
        prev_loop: asyncio.AbstractEventLoop | None = (
            asyncio.get_event_loop_policy().get_event_loop()
        )
    except RuntimeError:
        prev_loop = None
    loop = asyncio.new_event_loop()
    h.loop = loop
    asyncio.set_event_loop(loop)
    try:
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        writable_engine = _create_engine(db_url)
        h.writable_engine = writable_engine
        loop.run_until_complete(_seed_tables(db_url))
        h.writable_session = async_sessionmaker(writable_engine, expire_on_commit=False)

        events_dir = tmp_path / "events"
        h.events_dir = events_dir

        clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
        h.clock = clock

        async def _setup() -> tuple[LifespanManager, httpx.AsyncClient]:
            app = build_app(base_dir=events_dir, db_url=db_url, clock=clock)
            mgr = LifespanManager(app, startup_timeout=30, shutdown_timeout=30)
            await mgr.__aenter__()
            transport = httpx.ASGITransport(app=mgr.app)
            client = httpx.AsyncClient(
                transport=transport,
                base_url="http://registry-api:8080",
            )
            return mgr, client

        h.lifespan, h.client = loop.run_until_complete(_setup())
        yield h
    finally:
        async def _teardown() -> None:
            if h.client is not None:
                await h.client.aclose()
            if h.lifespan is not None:
                await h.lifespan.__aexit__(None, None, None)
            if h.writable_engine is not None:
                await h.writable_engine.dispose()

        loop.run_until_complete(_teardown())
        asyncio.set_event_loop(prev_loop)
        loop.close()


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
    ]
    for type_name, version, payload_cls in _payloads:
        with contextlib.suppress(KeyError, ValueError):
            _register_event(type_name, version, payload_cls)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
            title="license-gate-test-task",
        )
        session.add(task)
        await session.commit()
    return task_id


async def _seed_license_flagged_event(
    session_maker,
    clock: FrozenClock,
    task_id: str,
    rng: Random,
) -> str:
    """Insert a task.license_flagged event row directly into the SQL Event table.

    The materializer is not running in the harness, so we bypass it and
    write directly to SQL. This is what _check_license_gate() queries.
    """
    event_id = new_event_id(clock=clock, rng=rng)
    payload = TaskLicenseFlaggedPayload(
        task_id=task_id,
        reason_code="gpl-contamination",
        file_list=["src/gpl_code.py"],
        detected_licenses=["GPL-3.0"],
    )
    async with session_maker() as session:
        event = Event(
            id=event_id,
            type="task.license_flagged",
            schema_version="1.0.0",
            emitted_at=clock.now(),
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor_kind="worker",
            actor_id="test-worker",
            task_id=task_id,
            request_id=new_request_id(rng=rng),
            payload_json=payload.model_dump_json(),
        )
        session.add(event)
        await session.commit()
    return event_id


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
        "X-Actor-ID": "test-operator",
    }
    return await client.post(
        f"/v1/tasks/{task_id}/decisions",
        json=body,
        headers=headers,
    )


async def _count_jsonl_events(events_dir, task_id: str) -> int:
    """Count JSONL event log lines for a given task.

    The materializer is not running in the harness, so we read the
    JSONL event log directly instead of querying the SQL Event table.
    ``task_id`` lives inside the ``payload`` field of the envelope.
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


async def _count_jsonl_events_by_type(
    events_dir, task_id: str, event_type: str
) -> int:
    """Count JSONL event lines for a task, filtered by envelope ``type``."""
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
            if (
                obj.get("type") == event_type
                and isinstance(payload, dict)
                and payload.get("task_id") == task_id
            ):
                count += 1
        except (json.JSONDecodeError, KeyError):
            continue
    return count


# ---------------------------------------------------------------------------
# Test: license flag blocks approval (AC: #1)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_license_flag_blocks_approval(harness: _Harness) -> None:
    """Seed license_flagged event in SQL, POST /approve without override → 409."""
    h = harness
    _assert_ready(h)

    rng = Random(42)

    async def _run() -> httpx.Response:
        task_id = await _seed_task(h.writable_session, h.clock)
        await _seed_license_flagged_event(h.writable_session, h.clock, task_id, rng)
        key = new_idempotency_key(rng=rng)
        return await _submit_decision(h.client, task_id, "approve", key, rng)

    resp = h.loop.run_until_complete(_run())

    assert resp.status_code == 409, (
        f"expected 409 (license gate blocked), got {resp.status_code}: {resp.text}"
    )
    assert "application/problem+json" in resp.headers.get("content-type", ""), (
        "expected RFC 7807 content-type application/problem+json"
    )
    body = resp.json()
    assert body.get("type") == "approval_blocked_by", (
        f"expected type='approval_blocked_by', got {body.get('type')}"
    )
    assert body.get("status") == 409, (
        f"expected status=409, got {body.get('status')}"
    )
    assert body.get("extensions", {}).get("reason") == "license_flag", (
        f"expected reason='license_flag', got {body.get('extensions', {}).get('reason')}"
    )


# ---------------------------------------------------------------------------
# Test: license override succeeds (AC: #2)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_license_override_approve(harness: _Harness) -> None:
    """Seed license_flagged event, POST /approve --override license → 202 + 2 events."""
    h = harness
    _assert_ready(h)

    rng = Random(42)

    async def _run() -> tuple[httpx.Response, str]:
        task_id = await _seed_task(h.writable_session, h.clock)
        await _seed_license_flagged_event(h.writable_session, h.clock, task_id, rng)
        key = new_idempotency_key(rng=rng)
        resp = await _submit_decision(
            h.client, task_id, "approve", key, rng, override="license"
        )
        return resp, task_id

    resp, task_id = h.loop.run_until_complete(_run())

    assert resp.status_code == 202, (
        f"expected 202 (override approved), got {resp.status_code}: {resp.text}"
    )

    # Verify both specific event types were emitted.
    approval_count = h.loop.run_until_complete(
        _count_jsonl_events_by_type(h.events_dir, task_id, "approval.granted")
    )
    override_count = h.loop.run_until_complete(
        _count_jsonl_events_by_type(h.events_dir, task_id, "tier3.license_override")
    )
    assert approval_count == 1, (
        f"expected 1 approval.granted event, got {approval_count}"
    )
    assert override_count == 1, (
        f"expected 1 tier3.license_override event, got {override_count}"
    )


# ---------------------------------------------------------------------------
# Test: happy path without license flag (AC: #1 negative case)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_approve_without_license_flag(harness: _Harness) -> None:
    """Seed task WITHOUT license flag, POST /approve → 202 + 1 event."""
    h = harness
    _assert_ready(h)

    rng = Random(42)

    async def _run() -> tuple[httpx.Response, str]:
        task_id = await _seed_task(h.writable_session, h.clock)
        # No _seed_license_flagged_event — no license flag.
        key = new_idempotency_key(rng=rng)
        resp = await _submit_decision(h.client, task_id, "approve", key, rng)
        return resp, task_id

    resp, task_id = h.loop.run_until_complete(_run())

    assert resp.status_code == 202, (
        f"expected 202 (normal approve), got {resp.status_code}: {resp.text}"
    )

    approval_count = h.loop.run_until_complete(
        _count_jsonl_events_by_type(h.events_dir, task_id, "approval.granted")
    )
    assert approval_count == 1, (
        f"expected exactly 1 approval.granted event, got {approval_count}"
    )


# ---------------------------------------------------------------------------
# Test: license flag does not block reject or stop (defense test)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_license_flag_does_not_block_reject_or_stop(harness: _Harness) -> None:
    """License gate only blocks approve — reject and stop succeed normally."""
    h = harness
    _assert_ready(h)

    rng = Random(42)

    async def _run() -> tuple[httpx.Response, httpx.Response]:
        # Seed one task for reject, one for stop.
        task_id_1 = await _seed_task(h.writable_session, h.clock)
        await _seed_license_flagged_event(h.writable_session, h.clock, task_id_1, rng)
        key_1 = new_idempotency_key(rng=rng)
        resp_reject = await _submit_decision(
            h.client, task_id_1, "reject", key_1, rng
        )

        task_id_2 = await _seed_task(h.writable_session, h.clock)
        await _seed_license_flagged_event(h.writable_session, h.clock, task_id_2, rng)
        key_2 = new_idempotency_key(rng=rng)
        resp_stop = await _submit_decision(
            h.client, task_id_2, "stop", key_2, rng
        )

        return resp_reject, resp_stop

    resp_reject, resp_stop = h.loop.run_until_complete(_run())

    assert resp_reject.status_code == 202, (
        f"reject should return 202 despite license flag, got "
        f"{resp_reject.status_code}: {resp_reject.text}"
    )
    assert resp_stop.status_code == 200, (
        f"stop should return 200 despite license flag, got "
        f"{resp_stop.status_code}: {resp_stop.text}"
    )
