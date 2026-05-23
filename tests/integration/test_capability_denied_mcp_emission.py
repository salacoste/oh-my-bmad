"""Story 11.2.2 AC4 — end-to-end capability.denied MCP-boundary counter increment.

Lives under ``tests/integration/`` (not inside any service or mcp-server)
so it may import from BOTH the task-registry / clawhip-bridge MCP servers
(producers) AND ``metrics_subscriber`` (consumer) without violating the
cross-service import gate (``scripts/check_imports.py`` exempts
``tests/`` — see Story 5.8 AC-7 / Architecture line 854).

Scope: trigger an MCP-boundary tier denial via task-registry → audit
envelope written to JSONL by clawhip-bridge's ``_emit`` → read envelope
back, drive through ``metrics_subscriber.app.metrics.update_for`` →
assert ``omb_capability_denied_total{tier=tier3, boundary=mcp}``
incremented to 1.0. Closes the producer→consumer loop for Epic 10
retro DD5 at the MCP boundary (HTTP boundary closed by Story 11.2.1).

Test architecture choice (documented in DAR):
    A pure stdio MCP-to-MCP subprocess harness is significantly more
    complex (process supervision, stdin/stdout marshalling, ready
    signaling) than what's needed to validate the wiring discipline.
    Instead we build BOTH the task-registry server and the clawhip-bridge
    server in-process, then wire the task-registry's ``EmitterHolder``
    with an in-process adapter that calls clawhip-bridge's ``emit_event``
    tool fn directly. This exercises:
      - task-registry decorator wrapping (Story 11.2.2 Phase 3 wiring)
      - emit_capability_denied_on_deny PD-1 fail-soft contract
      - clawhip-bridge's EventLogWriter → JSONL on-disk artifact
      - metrics-subscriber update_for dispatcher
    …without the stdio plumbing overhead. The stdio entry point itself
    is exercised by the existing ``test_server.py`` subprocess tests for
    each MCP server.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from capabilities import Tier
from capabilities.tiers import CapabilityDenied
from clawhip_bridge_mcp.server import build_server as build_clawhip_server
from events import FROZEN_EPOCH, FrozenClock
from events.canonical import from_canonical_json
from events.envelope import EventEnvelope
from events.payloads import CapabilityDeniedPayload
from metrics_subscriber.app.metrics import build_collectors, update_for
from prometheus_client import CollectorRegistry
from registry_state.schema import Base, Task  # noqa: IMP001 — tests/ exempt
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from task_registry_mcp.adapters.clawhip_client import EmitterHolder
from task_registry_mcp.app.main import build_server as build_task_registry_server

_FROZEN_MONO_NS = 1_000_000
_VALID_TRACE_ID = "01917e5c-a7d1-7000-8abc-0123456789ab"
_SEEDED_TASK_ID = "t-00000001-0001-7000-8000-000000000001"


@pytest.fixture
def fixed_clock() -> FrozenClock:
    return FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)


@pytest_asyncio.fixture
async def db_session_maker(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Create an in-memory SQLite schema for the task-registry server."""
    engine: AsyncEngine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_conn: object, _rec: object) -> None:
        cur = dbapi_conn.cursor()  # type: ignore[union-attr]
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        session.add(
            Task(
                id=_SEEDED_TASK_ID,
                status="executing",
                created_at=FROZEN_EPOCH,
                updated_at=FROZEN_EPOCH,
                actor_kind="operator",
                actor_id="op-1",
                title="integration test task",
            )
        )
        await session.commit()
    return sm


class _InProcessClawhipAdapter:
    """Adapter exposing ``emit_event(type, payload, ...)`` against an in-process clawhip-bridge.

    Substitutes for the stdio ``ClawhipBridgeClient`` in tests — calls the
    clawhip-bridge ``emit_event`` tool fn directly. The decorator's PD-1
    fail-soft contract treats this as a regular emitter; the on-disk
    envelope artifact (and downstream counter increment) is identical to
    the stdio path.
    """

    def __init__(self, clawhip_mcp: object) -> None:
        self._fn = clawhip_mcp._tool_manager._tools["emit_event"].fn  # type: ignore[attr-defined]

    async def emit_event(
        self,
        event_type: str,
        payload: dict[str, object],
        *,
        caller_trace_id: str | None = None,
        parent_event_id: str | None = None,
    ) -> None:
        await self._fn(
            type=event_type,
            payload=payload,
            caller_trace_id=caller_trace_id or _VALID_TRACE_ID,
            parent_event_id=parent_event_id,
        )


@pytest.mark.asyncio
async def test_mcp_capability_denied_emits_envelope_and_increments_counter(
    tmp_path: Path,
    fixed_clock: FrozenClock,
    db_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """AC1+AC4+AC6+AC7: tier-denied MCP tool call emits envelope; counter increments end-to-end."""
    events_dir = tmp_path / "events"
    events_dir.mkdir()

    # Build a real clawhip-bridge MCP server (writes JSONL to events_dir).
    clawhip_mcp = build_clawhip_server(
        base_dir=events_dir,
        clock=fixed_clock,
        actor_kind="system",
        actor_id="clawhip-bridge-mcp",
    )

    # Wire the task-registry server with an in-process emitter pointed at clawhip-bridge.
    emitter_holder = EmitterHolder()
    # Story 11.2.2 test-only: substitute the stdio ClawhipBridgeClient with an
    # in-process adapter. The adapter exposes ``emit_event`` with the same
    # signature so EmitterHolder.emit_event forwards transparently.
    emitter_holder.client = _InProcessClawhipAdapter(clawhip_mcp)  # type: ignore[assignment]

    # Build task-registry server via the factory (no-emitter path), then
    # clear its tool registry and re-register with the in-process emitter
    # wired in. This avoids stdio subprocess plumbing (see module docstring).
    from task_registry_mcp.handlers.tools import register_tools

    task_mcp = build_task_registry_server(
        actor_kind="worker",
        actor_id="worker-under-tier",
        _session_maker=db_session_maker,
    )
    task_mcp._tool_manager._tools.clear()  # type: ignore[attr-defined]
    register_tools(
        task_mcp,
        db_session_maker,
        "worker",
        "worker-under-tier",
        emitter_holder=emitter_holder,
    )

    # Drive the denial: force task.add_note to require Tier.THREE — worker can't reach it.
    patched_tier_map = {
        "task.add_note": Tier.THREE,
        "task.attach_artifact": Tier.ONE,
        "task.emit_event": Tier.ONE,
    }
    fn = task_mcp._tool_manager._tools["task_add_note"].fn  # type: ignore[attr-defined]
    with (
        patch("task_registry_mcp.handlers.tools.TIER_MAP", patched_tier_map),
        pytest.raises(CapabilityDenied) as exc_info,  # AC6: original error semantics preserved
    ):
        await fn(
            task_id=_SEEDED_TASK_ID,
            note="should be denied",
            caller_trace_id=_VALID_TRACE_ID,
        )

    # AC6 verification: the original CapabilityDenied (not some wrapper) was raised.
    assert exc_info.value.required_tier == 3
    assert exc_info.value.actor_kind == "worker"

    # Producer side: scan JSONL for the emitted envelope.
    envelopes: list[EventEnvelope] = []
    for log_file in sorted(events_dir.glob("*.jsonl")):
        for raw in log_file.read_bytes().splitlines():
            if not raw.strip():
                continue
            env = from_canonical_json(raw)
            if env.type == "capability.denied":
                envelopes.append(env)

    assert len(envelopes) == 1, (
        f"expected exactly 1 capability.denied envelope; got {len(envelopes)}"
    )
    env = envelopes[0]
    assert env.schema_version == "1.1.0"
    # PP6 mirror: round-trip through Pydantic so a field rename fails this test.
    payload = CapabilityDeniedPayload.model_validate(env.payload)
    assert payload.tier == "tier3"
    assert payload.boundary == "mcp"
    assert payload.attempted_action == "task.add_note"
    assert payload.actor_id == "worker-under-tier"

    # Envelope actor: OQ-2 — system-stamped emitter id matches the clawhip-bridge configuration.
    assert env.actor.kind == "system"
    assert env.actor.id == "clawhip-bridge-mcp"

    # Consumer side: dispatch through the metrics-subscriber materializer.
    registry = CollectorRegistry()
    state = build_collectors(registry=registry)
    update_for(state, env)

    value = registry.get_sample_value(
        "omb_capability_denied_total",
        {"tier": "tier3", "boundary": "mcp"},
    )
    assert value == 1.0, (
        f"omb_capability_denied_total{{tier=tier3, boundary=mcp}} expected 1.0, got {value}"
    )

    # And the family-level counter should also reflect the new event.
    family_value = registry.get_sample_value(
        "omb_events_appended_total",
        {"event_family": "capability"},
    )
    assert family_value == 1.0, (
        f"omb_events_appended_total{{event_family=capability}} expected 1.0, got {family_value}"
    )


@pytest.mark.asyncio
async def test_mcp_capability_denied_pd1_fail_soft_when_emitter_broken(
    tmp_path: Path,
    fixed_clock: FrozenClock,
    db_session_maker: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC7: emitter exception is swallowed; original CapabilityDenied still re-raises (AC6).

    PQ4 (pass-1 review): also asserts the emission failure was LOGGED at
    ERROR (``capability_denied_emission_failed``) — pre-PQ4 the test only
    verified the re-raise, so a future refactor that narrowed the broad
    ``except Exception`` would have passed silently while emission failures
    became unobservable in production.
    """
    import logging

    class _BrokenEmitter:
        async def emit_event(self, *_a: object, **_kw: object) -> None:
            raise RuntimeError("simulated broken pipe to clawhip-bridge")

    emitter_holder = EmitterHolder()
    emitter_holder.client = _BrokenEmitter()  # type: ignore[assignment]

    from task_registry_mcp.handlers.tools import register_tools

    task_mcp = build_task_registry_server(
        actor_kind="worker",
        actor_id="worker-under-tier",
        _session_maker=db_session_maker,
    )
    task_mcp._tool_manager._tools.clear()  # type: ignore[attr-defined]
    register_tools(
        task_mcp,
        db_session_maker,
        "worker",
        "worker-under-tier",
        emitter_holder=emitter_holder,
    )

    patched_tier_map = {
        "task.add_note": Tier.THREE,
        "task.attach_artifact": Tier.ONE,
        "task.emit_event": Tier.ONE,
    }
    fn = task_mcp._tool_manager._tools["task_add_note"].fn  # type: ignore[attr-defined]
    with (
        caplog.at_level(logging.ERROR, logger="capabilities.emit"),
        patch("task_registry_mcp.handlers.tools.TIER_MAP", patched_tier_map),
        pytest.raises(CapabilityDenied),  # AC6 must still be honored
    ):
        await fn(
            task_id=_SEEDED_TASK_ID,
            note="denied even with broken emitter",
            caller_trace_id=_VALID_TRACE_ID,
        )

    # PQ4: confirm PD-1 fail-soft logged at ERROR so emission failures are
    # observable to operators (counter sits at 0 silently otherwise).
    failure_logs = [
        rec for rec in caplog.records if rec.message == "capability_denied_emission_failed"
    ]
    assert failure_logs, (
        "PD-1 fail-soft contract: broken emitter must emit "
        "`capability_denied_emission_failed` log line at ERROR"
    )


@pytest.mark.asyncio
async def test_clawhip_bridge_self_deny_emits_audit_envelope(
    tmp_path: Path,
    fixed_clock: FrozenClock,
) -> None:
    """AC3-A: clawhip-bridge's own ``check_tier`` denial routes through internal ``_emit``.

    Drives ``emit_event`` with a constrained actor (worker can only reach
    Tier.ONE; we patch the tier map to require Tier.THREE) and asserts:
    1. CapabilityDenied surfaces (AC6).
    2. capability.denied envelope lands in JSONL with boundary=mcp.
    """
    from clawhip_bridge_mcp.server import TIER_MAP as CB_TIER_MAP

    events_dir = tmp_path / "events"
    events_dir.mkdir()
    clawhip_mcp = build_clawhip_server(
        base_dir=events_dir,
        clock=fixed_clock,
        actor_kind="worker",
        actor_id="worker-bridge-test",
    )
    fn = clawhip_mcp._tool_manager._tools["emit_event"].fn  # type: ignore[attr-defined]

    patched_map = {**CB_TIER_MAP, "emit_event": Tier.THREE}
    with (
        patch("clawhip_bridge_mcp.server.TIER_MAP", patched_map),
        pytest.raises(CapabilityDenied),
    ):
        await fn(
            type="task.summary_emitted",
            payload={"task_id": _SEEDED_TASK_ID, "summary": "self-deny test"},
            caller_trace_id=_VALID_TRACE_ID,
        )

    # The audit envelope must be on disk despite the actor's denial (AC3-A).
    audit_envelopes: list[EventEnvelope] = []
    for log_file in sorted(events_dir.glob("*.jsonl")):
        for raw in log_file.read_bytes().splitlines():
            if not raw.strip():
                continue
            env = from_canonical_json(raw)
            if env.type == "capability.denied":
                audit_envelopes.append(env)

    assert len(audit_envelopes) == 1, (
        f"expected exactly 1 capability.denied envelope from self-deny path; "
        f"got {len(audit_envelopes)}"
    )
    payload = CapabilityDeniedPayload.model_validate(audit_envelopes[0].payload)
    assert payload.tier == "tier3"
    assert payload.boundary == "mcp"
    assert payload.attempted_action == "emit_event"
    assert payload.actor_id == "worker-bridge-test"
