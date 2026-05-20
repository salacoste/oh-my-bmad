"""Integration tests for HMAC signing in the /decisions handler (Story 11.1).

Covers AC4 (paired-event emission), AC5 (NFR-S10 isolation in the event log),
and AC6 (8-char prefix log discipline). Settings + pure-fn tests live in
``test_approval_signing.py``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 test fixture builds in-memory SQLite via registry-state's schema; no prod cross-service coupling
    create_engine,
)
from registry_state.domain.event_types import (  # noqa: IMP001 — test fixture re-registers canonical event types after sibling tests' unregister_all() (Epic 8 retro debt #3)
    ensure_registered,
)
from registry_state.schema import (  # noqa: IMP001 test fixture imports tables for Base.metadata.create_all seeding
    Base,
    Task,
)

from registry_api.adapters.approval_signing import compute_approval_hmac
from registry_api.app import build_app
from registry_api.settings import ApprovalSigningSettings


@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    """Re-register canonical event types before every test (see test_decisions.py)."""
    ensure_registered()


_FROZEN_MONO_NS = 1_000_000
_TID_PLAN_READY = "t-00000000-0000-7000-8000-000000000001"
_TID_AWAITING = "t-00000000-0000-7000-8000-000000000002"
_TID_EXECUTING = "t-00000000-0000-7000-8000-000000000003"

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
        now = datetime(2026, 1, 1, tzinfo=UTC)
        op = {
            "actor_kind": "operator",
            "actor_id": "test-op",
            "created_at": now,
            "updated_at": now,
        }
        rows = [
            {"id": _TID_PLAN_READY, "status": "plan_ready", "title": "Plan ready", **op},
            {"id": _TID_AWAITING, "status": "awaiting_approval", "title": "Awaiting", **op},
            {"id": _TID_EXECUTING, "status": "executing", "title": "Executing", **op},
        ]
        for row in rows:
            await conn.execute(Task.__table__.insert(), row)  # type: ignore[attr-defined]  # SQLAlchemy stubs return FromClause; Table.__table__ resolves at runtime
    await engine.dispose()


@pytest_asyncio.fixture
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
        events = _read_jsonl_events(events_dir)
        assert len(events) == 2, f"expected 2 events, got {[e['type'] for e in events]}"

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
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """AC4 / D2: approve without key → only approval.granted + warning."""
        with caplog.at_level(logging.WARNING, logger="registry_api.routes.decisions"):
            r = await unsigned_client.post(
                f"/v1/tasks/{_TID_PLAN_READY}/decisions",
                json={"action": "approve"},
            )
        assert r.status_code == 202

        events_dir: Path = unsigned_client._events_dir  # type: ignore[attr-defined]
        events = _read_jsonl_events(events_dir)
        types = [e["type"] for e in events]
        assert types == ["approval.granted"]

        # Structured warning was emitted.
        assert any(
            "approval_signing_disabled_missing_hmac_key" in record.message
            for record in caplog.records
        )

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
        events = _read_jsonl_events(events_dir)
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
        events = _read_jsonl_events(events_dir)
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
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """AC5: full caplog grep — key never appears in any log record."""
        with caplog.at_level(logging.DEBUG):
            r = await signing_client.post(
                f"/v1/tasks/{_TID_PLAN_READY}/decisions",
                json={"action": "approve"},
            )
            assert r.status_code == 202

        for record in caplog.records:
            # Check both the formatted message AND the raw args (defensive
            # against future code that passes the key as a logger arg).
            assert _HMAC_KEY_VALUE not in record.getMessage()
            for arg in record.args or ():
                assert _HMAC_KEY_VALUE not in str(arg)


# ---------------------------------------------------------------------------
# AC6 — Structured-log discipline (8-char prefix only)
# ---------------------------------------------------------------------------


class TestApprovalSignedLogDiscipline:
    """AC6 / D5: INFO log contains the 8-char HMAC prefix, NOT the full value."""

    @pytest.mark.asyncio
    async def test_approval_signed_log_event_emitted_with_8char_prefix(
        self,
        signing_client: AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """AC6: log message includes ``hmac_sha256_prefix=<8 hex chars>``.

        The full 64-char HMAC MUST NOT appear at INFO — that's bloat.
        Operators can correlate via the prefix; full HMAC lives in events.
        """
        with caplog.at_level(logging.INFO, logger="registry_api.routes.decisions"):
            r = await signing_client.post(
                f"/v1/tasks/{_TID_PLAN_READY}/decisions",
                json={"action": "approve"},
            )
            assert r.status_code == 202

        # Find the approval_signed log.
        signed_records = [r for r in caplog.records if "approval_signed" in r.getMessage()]
        assert len(signed_records) >= 1
        msg = signed_records[0].getMessage()
        assert "hmac_sha256_prefix=" in msg

        # Extract the 8 hex chars after the prefix marker.
        prefix_part = msg.split("hmac_sha256_prefix=", 1)[1]
        # The prefix is followed by EOL or whitespace.
        prefix_value = prefix_part.split()[0] if prefix_part.split() else prefix_part
        assert len(prefix_value) == 8
        assert all(c in "0123456789abcdef" for c in prefix_value)

        # Cross-check: the full HMAC from the event log starts with this prefix.
        events_dir: Path = signing_client._events_dir  # type: ignore[attr-defined]
        events = _read_jsonl_events(events_dir)
        signed = next(e for e in events if e["type"] == "task.approval_signed")
        signed_payload = signed["payload"]
        assert isinstance(signed_payload, dict)
        full_hmac = str(signed_payload["hmac_sha256"])
        assert full_hmac.startswith(prefix_value)
        # And the full 64-char HMAC is NOT in the log message (only prefix).
        assert full_hmac not in msg
