"""Integration tests for HMAC signing in the /decisions handler (Story 11.1).

Covers AC4 (paired-event emission), AC5 (NFR-S10 isolation in the event log),
and AC6 (8-char prefix log discipline). Settings + pure-fn tests live in
``test_approval_signing.py``.

NOTE (Story 8.7.5 PP7): Schema-registry registration is handled by the
repo-root conftest.py session-scoped autouse fixture. If new sibling tests
in this package call unregister_all(), they MUST add a function-scoped
teardown calling ensure_registered() to restore canonical state. See
docs/testing-guide.md "Schema-registry isolation in tests" + Story 7e4ffec.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
import structlog.testing
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 test fixture builds in-memory SQLite via registry-state's schema; no prod cross-service coupling
    create_engine,
)
from registry_state.schema import (  # noqa: IMP001 test fixture imports tables for Base.metadata.create_all seeding
    Base,
    Task,
)

from registry_api.adapters.approval_signing import compute_approval_hmac
from registry_api.app import build_app
from registry_api.settings import ApprovalSigningSettings

_FROZEN_MONO_NS = 1_000_000
_TID_PLAN_READY = "t-00000000-0000-7000-8000-000000000001"
# P1-M5: ACTION_VALID_STATES["approve"] = {"plan_ready", "awaiting_approval", "blocked"}.
# _TID_AWAITING is seeded with status="awaiting_approval" — a valid pre-state for
# "approve", "reject", and "stop" actions (verified in lifecycle.py).
_TID_AWAITING = "t-00000000-0000-7000-8000-000000000002"
_TID_EXECUTING = "t-00000000-0000-7000-8000-000000000003"
# P1-L4: retry requires status in {"blocked", "failed"}.
_TID_BLOCKED = "t-00000000-0000-7000-8000-000000000004"

# AC5 sentinel — 64-char hex (matches `openssl rand -hex 32` shape, but
# distinct so test assertions can grep for absence).
_HMAC_KEY_VALUE = "deadbeef" * 8  # 64 hex chars
_HMAC_KEY = SecretStr(_HMAC_KEY_VALUE)


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _seed_tables_with_tasks(db_url: str) -> None:
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # P1-L2: use FROZEN_EPOCH (canonical test baseline) instead of hardcoded datetime.
        op = {
            "actor_kind": "operator",
            "actor_id": "test-op",
            "created_at": FROZEN_EPOCH,
            "updated_at": FROZEN_EPOCH,
        }
        rows = [
            {"id": _TID_PLAN_READY, "status": "plan_ready", "title": "Plan ready", **op},
            {"id": _TID_AWAITING, "status": "awaiting_approval", "title": "Awaiting", **op},
            {"id": _TID_EXECUTING, "status": "executing", "title": "Executing", **op},
            {"id": _TID_BLOCKED, "status": "blocked", "title": "Blocked", **op},
        ]
        for row in rows:
            await conn.execute(Task.__table__.insert(), row)  # type: ignore[attr-defined]  # SQLAlchemy stubs return FromClause; Table.__table__ resolves at runtime
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="function")  # Story 8.7.6 PP2 — LifespanManager bg state
async def signing_client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    """Client with OPERATOR_HMAC_KEY set via explicit settings injection.

    Avoids env-var mutation (test isolation). Mirrors the production wiring
    where ``ApprovalSigningSettings.from_env()`` is the default and explicit
    injection is the testing seam.
    """
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables_with_tasks(db_url)
    events_dir = tmp_path / "events"
    clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)

    signing_settings = ApprovalSigningSettings(operator_hmac_key=_HMAC_KEY)
    app = build_app(
        base_dir=events_dir,
        db_url=db_url,
        clock=clock,
        signing_settings=signing_settings,
    )

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app),
            base_url="http://testserver",
        ) as client,
    ):
        # Stash the events_dir on the client for tests to grep.
        client._events_dir = events_dir  # type: ignore[attr-defined]
        yield client


@pytest_asyncio.fixture
async def unsigned_client(tmp_path: Path) -> AsyncGenerator[AsyncClient, None]:
    """Client with OPERATOR_HMAC_KEY=None — signing disabled."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables_with_tasks(db_url)
    events_dir = tmp_path / "events"
    clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)

    signing_settings = ApprovalSigningSettings(operator_hmac_key=None)
    app = build_app(
        base_dir=events_dir,
        db_url=db_url,
        clock=clock,
        signing_settings=signing_settings,
    )

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app),
            base_url="http://testserver",
        ) as client,
    ):
        client._events_dir = events_dir  # type: ignore[attr-defined]
        yield client


def _read_jsonl_events(events_dir: Path) -> list[dict[str, object]]:
    """Read all JSONL event files in chronological order."""
    out: list[dict[str, object]] = []
    for f in sorted(events_dir.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# AC4 — Handler emits paired task.approval_signed
# ---------------------------------------------------------------------------


class TestApproveEmitsPairedSignedEvent:
    """AC4: approve with OPERATOR_HMAC_KEY set emits BOTH events."""

    @pytest.mark.asyncio
    async def test_approve_handler_emits_paired_signed_event(
        self,
        signing_client: AsyncClient,
    ) -> None:
        """AC4: approve → approval.granted FOLLOWED BY task.approval_signed.

        Both share task_id, trace_id, decided_at. HMAC value matches a
        manual recomputation.
        """
        r = await signing_client.post(
            f"/v1/tasks/{_TID_PLAN_READY}/decisions",
            json={"action": "approve"},
        )
        assert r.status_code == 202

        events_dir: Path = signing_client._events_dir  # type: ignore[attr-defined]
        all_events = _read_jsonl_events(events_dir)
        # Story 11.5: lifespan now emits key.rotated on first boot (rotation
        # detector). Filter it out so this test remains focused on the paired
        # approval events that the /decisions handler emits.
        events = [e for e in all_events if e["type"] != "key.rotated"]
        all_types = [e["type"] for e in all_events]
        # Story 11.5 PP1 — count key.rotated explicitly. Pre-PP1 the filter
        # silently stripped ALL key.rotated events; a future multi-emission
        # bug would pass tests. Asserting exactly one first-boot rotation
        # event catches regressions of the exactly-once invariant.
        key_rotated_count = sum(1 for e in all_events if e["type"] == "key.rotated")
        assert key_rotated_count == 1, (
            f"expected exactly 1 first-boot key.rotated, got {key_rotated_count} "
            f"(types: {all_types})"
        )
        assert len(events) == 2, f"expected 2 approval events, got {all_types}"

        granted, signed = events
        assert granted["type"] == "approval.granted"
        assert signed["type"] == "task.approval_signed"

        # Ordering invariant: signed comes AFTER granted in the log.
        assert events.index(granted) < events.index(signed)

        # Shared identifiers.
        granted_payload = granted["payload"]
        signed_payload = signed["payload"]
        assert isinstance(granted_payload, dict)
        assert isinstance(signed_payload, dict)
        assert granted_payload["task_id"] == signed_payload["task_id"] == _TID_PLAN_READY
        assert granted_payload["decision_id"] == signed_payload["decision_id"]
        assert granted_payload["actor_id"] == signed_payload["actor_id"]
        assert granted["trace_id"] == signed["trace_id"]
        # parent_event_id wiring (signed.parent = granted.event_id).
        assert signed["parent_event_id"] == granted["event_id"]

        # Decided_at deterministic (FrozenClock) — both events share it.
        assert granted["emitted_at"] == signed["emitted_at"]

        # HMAC matches manual recomputation against the same inputs.
        # signed.timestamp is the canonical signing timestamp.
        expected_hmac = compute_approval_hmac(
            key=_HMAC_KEY,
            task_id=str(signed_payload["task_id"]),
            action="approve",
            timestamp=datetime.fromisoformat(str(signed_payload["timestamp"])),
            actor_id=str(signed_payload["actor_id"]),
        )
        assert signed_payload["hmac_sha256"] == expected_hmac
        # Shape: 64 lowercase hex chars.
        hmac_value = str(signed_payload["hmac_sha256"])
        assert len(hmac_value) == 64
        assert all(c in "0123456789abcdef" for c in hmac_value)

    @pytest.mark.asyncio
    async def test_approve_handler_skips_signed_event_when_no_key(
        self,
        unsigned_client: AsyncClient,
    ) -> None:
        """AC4 / D2: approve without key → only approval.granted + warning."""
        with structlog.testing.capture_logs() as cap:
            r = await unsigned_client.post(
                f"/v1/tasks/{_TID_PLAN_READY}/decisions",
                json={"action": "approve"},
            )
        assert r.status_code == 202

        events_dir: Path = unsigned_client._events_dir  # type: ignore[attr-defined]
        events = _read_jsonl_events(events_dir)
        types = [e["type"] for e in events]
        assert types == ["approval.granted"]

        # Structured warning was emitted (structlog keyword-arg form, P1-H5).
        assert any(ev.get("event") == "approval_signing_disabled_missing_hmac_key" for ev in cap)

    @pytest.mark.asyncio
    async def test_approve_handler_reject_action_does_not_emit_signed_event(
        self,
        signing_client: AsyncClient,
    ) -> None:
        """AC4 / FR64: reject is NOT signed even when key is set."""
        r = await signing_client.post(
            f"/v1/tasks/{_TID_AWAITING}/decisions",
            json={"action": "reject", "reason": "Bad plan"},
        )
        assert r.status_code == 202

        events_dir: Path = signing_client._events_dir  # type: ignore[attr-defined]
        # Story 11.5: filter key.rotated (first-boot event from lifespan startup).
        events = [e for e in _read_jsonl_events(events_dir) if e["type"] != "key.rotated"]
        types = [e["type"] for e in events]
        assert types == ["approval.rejected"]
        assert "task.approval_signed" not in types

    @pytest.mark.asyncio
    async def test_stop_action_does_not_emit_signed_event(
        self,
        signing_client: AsyncClient,
    ) -> None:
        """AC4 / FR64: stop is NOT signed."""
        r = await signing_client.post(
            f"/v1/tasks/{_TID_EXECUTING}/decisions",
            json={"action": "stop"},
        )
        assert r.status_code == 200
        events_dir: Path = signing_client._events_dir  # type: ignore[attr-defined]
        # Story 11.5: filter key.rotated (first-boot event from lifespan startup).
        events = [e for e in _read_jsonl_events(events_dir) if e["type"] != "key.rotated"]
        assert [e["type"] for e in events] == ["task.stop_requested"]


# ---------------------------------------------------------------------------
# AC5 — NFR-S10 key never leaks into events / logs
# ---------------------------------------------------------------------------


class TestHmacKeyIsolation:
    """AC5: full-tree grep + caplog grep prove the key never lands anywhere."""

    @pytest.mark.asyncio
    async def test_hmac_key_isolation_no_leak_in_events(
        self,
        signing_client: AsyncClient,
    ) -> None:
        """AC5: emit 3 approval events, grep the JSONL log for the key.

        Asserts ZERO matches. The HMAC value is the ONLY key-derived
        material allowed in events; the key itself never appears.
        """
        for tid in (_TID_PLAN_READY, _TID_AWAITING):
            r = await signing_client.post(
                f"/v1/tasks/{tid}/decisions",
                json={"action": "approve"},
            )
            assert r.status_code == 202

        events_dir: Path = signing_client._events_dir  # type: ignore[attr-defined]
        # Full-tree grep.
        for f in events_dir.glob("*.jsonl"):
            content = f.read_text()
            assert _HMAC_KEY_VALUE not in content, f"HMAC key leaked into event log file {f.name}"
            # Also assert SecretStr's repr form is absent.
            assert "SecretStr" not in content

    @pytest.mark.asyncio
    async def test_hmac_key_isolation_no_leak_in_logs(
        self,
        signing_client: AsyncClient,
    ) -> None:
        """AC5 / P1-M1: capture_logs() grep — key never appears in any structlog event.

        Uses structlog.testing.capture_logs() instead of caplog (P1-M1): after
        the P1-H5 structlog migration, caplog captures stdlib log records but
        structlog events are emitted via structlog's own pipeline and may bypass
        caplog depending on configuration.  capture_logs() is the correct tool
        for structlog event interception.
        """
        with structlog.testing.capture_logs() as cap:
            r = await signing_client.post(
                f"/v1/tasks/{_TID_PLAN_READY}/decisions",
                json={"action": "approve"},
            )
        assert r.status_code == 202

        # Full event-dict grep: key must not appear in any field of any event.
        for event_dict in cap:
            for key, value in event_dict.items():
                assert _HMAC_KEY_VALUE not in str(value), (
                    f"HMAC key sentinel leaked in structlog event field {key!r}"
                )


# ---------------------------------------------------------------------------
# AC6 — Structured-log discipline (8-char prefix only)
# ---------------------------------------------------------------------------


class TestApprovalSignedLogDiscipline:
    """AC6 / D5: INFO log contains the 8-char HMAC prefix, NOT the full value."""

    @pytest.mark.asyncio
    async def test_approval_signed_log_event_emitted_with_8char_prefix(
        self,
        signing_client: AsyncClient,
    ) -> None:
        """AC6: structlog event 'approval_signed' has hmac_sha256_prefix field (8 hex chars).

        The full 64-char HMAC MUST NOT appear at INFO — that's bloat.
        Operators can correlate via the prefix; full HMAC lives in events.
        Uses structlog.testing.capture_logs() (P1-H5 migration: decisions.py
        now uses structlog keyword-arg form, not stdlib %s interpolation).
        """
        with structlog.testing.capture_logs() as cap:
            r = await signing_client.post(
                f"/v1/tasks/{_TID_PLAN_READY}/decisions",
                json={"action": "approve"},
            )
        assert r.status_code == 202

        # Find the approval_signed structlog event.
        signed_events = [ev for ev in cap if ev.get("event") == "approval_signed"]
        assert len(signed_events) >= 1, (
            f"No 'approval_signed' structlog event found; got {[ev.get('event') for ev in cap]}"
        )
        ev = signed_events[0]

        # Keyword field hmac_sha256_prefix present and 8 hex chars.
        assert "hmac_sha256_prefix" in ev, f"hmac_sha256_prefix missing from event: {ev}"
        prefix_value = str(ev["hmac_sha256_prefix"])
        assert len(prefix_value) == 8, f"expected 8 hex chars, got {prefix_value!r}"
        assert all(c in "0123456789abcdef" for c in prefix_value)

        # Cross-check: the full HMAC from the event log starts with this prefix.
        events_dir: Path = signing_client._events_dir  # type: ignore[attr-defined]
        events = _read_jsonl_events(events_dir)
        signed = next(e for e in events if e["type"] == "task.approval_signed")
        signed_payload = signed["payload"]
        assert isinstance(signed_payload, dict)
        full_hmac = str(signed_payload["hmac_sha256"])
        assert full_hmac.startswith(prefix_value)
        # And the full 64-char HMAC is NOT in the structlog event (only prefix).
        for key, value in ev.items():
            assert full_hmac not in str(value), f"Full HMAC leaked in structlog field {key!r}"


# ---------------------------------------------------------------------------
# P1-L4 — retry action does not emit task.approval_signed
# ---------------------------------------------------------------------------


class TestRetryActionDoesNotEmitSignedEvent:
    """P1-L4: retry is NOT signed (mirrors test_reject / test_stop pattern)."""

    @pytest.mark.asyncio
    async def test_retry_action_does_not_emit_signed_event(
        self,
        signing_client: AsyncClient,
    ) -> None:
        """P1-L4 / FR64: retry is NOT signed even when key is set.

        Spec: only 'approve' emits task.approval_signed sibling.
        reject + stop tests already cover this; retry is the missing case.
        """
        r = await signing_client.post(
            f"/v1/tasks/{_TID_BLOCKED}/decisions",
            json={"action": "retry"},
        )
        assert r.status_code == 200

        events_dir: Path = signing_client._events_dir  # type: ignore[attr-defined]
        # Story 11.5: filter key.rotated (first-boot event from lifespan startup).
        events = [e for e in _read_jsonl_events(events_dir) if e["type"] != "key.rotated"]
        types = [e["type"] for e in events]
        assert types == ["task.retry_requested"]
        assert "task.approval_signed" not in types


# ---------------------------------------------------------------------------
# P1-H4 — approval_signed append failure is non-fatal
# ---------------------------------------------------------------------------


class TestApprovalSignedAppendFailureNonFatal:
    """P1-H4: if task.approval_signed append raises, approval.granted is durable."""

    @pytest.mark.asyncio
    async def test_approval_signed_append_failure_does_not_crash_handler(
        self,
        tmp_path: Path,
    ) -> None:
        """P1-H4: second writer.append (signed sibling) failure → 202, warning logged.

        approval.granted is already durable when the signed sibling append
        fires.  A failure there must not re-raise (which would 500 the client
        and leave a confusing half-state).  The HMAC can be recomputed offline
        via just verify-approval (Story 11.4).

        Uses a fresh fixture (not signing_client) so we can intercept
        app.state.writer.append after lifespan starts.
        """
        db_path = tmp_path / "state.sqlite3"
        db_url = _db_url(db_path)
        await _seed_tables_with_tasks(db_url)
        events_dir = tmp_path / "events"
        clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
        signing_settings = ApprovalSigningSettings(operator_hmac_key=_HMAC_KEY)
        app = build_app(
            base_dir=events_dir,
            db_url=db_url,
            clock=clock,
            signing_settings=signing_settings,
        )

        async with (
            LifespanManager(app) as manager,
            AsyncClient(
                transport=ASGITransport(app=manager.app),
                base_url="http://testserver",
            ) as client,
        ):
            # Intercept the live writer instance's append method.
            # app.state is populated during lifespan; access via the original
            # app object (manager.app is the lifespan-wrapped callable, not
            # the FastAPI instance that carries .state).
            writer = app.state.writer
            real_append = writer.append
            call_count = 0

            async def _failing_append(envelope: object) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OSError("simulated disk full — P1-H4 test")
                await real_append(envelope)

            writer.append = _failing_append  # noqa: F821 — runtime monkey-patch for test isolation

            with structlog.testing.capture_logs() as cap:
                r = await client.post(
                    f"/v1/tasks/{_TID_PLAN_READY}/decisions",
                    json={"action": "approve"},
                )

        # Handler must still return 202 (approval.granted is durable).
        assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"

        # Warning was emitted with the correct event name.
        warning_events = [
            ev for ev in cap if ev.get("event") == "approval_signed_emit_failed_approval_durable"
        ]
        assert len(warning_events) >= 1, (
            f"Expected 'approval_signed_emit_failed_approval_durable' warning; "
            f"got events: {[ev.get('event') for ev in cap]}"
        )
        assert warning_events[0].get("error_type") == "OSError"
