"""Cross-service trace_id continuity E2E (NFR-O7 / FR59a).

Closes traceability gap G3 (MEDIUM). The pre-existing coverage only proved
trace_id continuity *within a single service*:

* ``services/registry-api/src/registry_api/test_trace.py`` exercises
  ``GET /v1/trace/{trace_id}`` but seeds the ``events`` table with raw
  single-source inserts (all ``actor_id="test-op"``).
* Per-entry-point binding tests prove a service stamps trace_id on its own
  emissions.

Neither threads ONE trace_id across MULTIPLE service code paths and then
queries the unified causal chain back via ``GET /v1/trace``.

NFR-O7 (the requirement under test):
    Every Phase-2 event carries a non-null ``trace_id``; an operator query
    for "all events with trace_id=X" returns the COMPLETE causal chain
    across registry-api, registry-state, telegram-gateway, worker-wrapper and
    the MCP servers, ordered by ``emitted_at_monotonic_ns``.

FR59a: the operator-facing ``GET /v1/trace/{trace_id}`` endpoint surfaces the
causal chain for forensic inspection.

What this test threads through the REAL emission + projection mechanisms
(no hand-rolled JSON — Epic 11 retro L6 test-fixture realism):

    real EventEnvelope.create  →  real EventLogWriter.append (canonical JSONL
    on disk, fdatasync, O_APPEND)  →  real events.log_reader.read_log_lines
    →  real registry-state Materializer.apply_many + register_default_handlers
    (the canonical JSONL→SQLite projection that wires envelope.trace_id into
    the events.trace_id column)  →  real registry-api build_app + in-process
    ASGI GET /v1/trace/{trace_id}.

Scoping caveat (documented honestly): a true *separately-running*
multi-process service stack is the Docker-compose journey tests' job
(``test_journey_1_overnight.py``, ``@pytest.mark.slow``). This test runs the
SAME production code paths in-process and proves the "across services"
property structurally: each in-trace event is built with a DISTINCT
``actor`` identity standing for a distinct service code path
(``registry-api`` operator ingress, ``worker-wrapper`` and
``orchestrator-adapter`` system emissions), exactly as the real services
stamp their own ``actor`` on their envelopes. The /trace response exposes
``actor.id`` per event, so the assertion that all three sources appear in one
trace_id query is the faithful in-process analog of "across services".
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from random import Random

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from events import (
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    FrozenClock,
    TaskCompletedPayload,
    TaskCreatedPayload,
    TaskExecutionStartedPayload,
    TaskPlanningStartedPayload,
    TaskStepCompletedPayload,
    new_event_id,
    new_session_id,
    new_task_id,
    new_uuid7,
)
from events.event_log_writer import EventLogWriter
from events.log_reader import read_log_lines
from events.schema_registry import register as _register_schema
from httpx import ASGITransport, AsyncClient
from registry_api.app import build_app  # noqa: IMP001 — in-process ASGI harness (see test_trace.py)
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — shared in-process state engine
    create_engine,
    get_session,
)
from registry_state.domain.handlers import (  # noqa: IMP001 — canonical JSONL→SQLite projection
    register_default_handlers,
)
from registry_state.domain.materializer import Materializer  # noqa: IMP001

# A frozen clock keeps emitted_at deterministic; emitted_at_monotonic_ns is
# supplied explicitly per event so the test controls causal ordering.
_FROZEN_CLOCK = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)


def _register_event_types() -> None:
    """Register the lifecycle payload models needed for envelope creation.

    Mirrors the explicit-registration pattern in
    ``tests/integration/test_task_thread_binding.py::_ensure_event_types`` —
    the ``events`` package only auto-registers deployment/replication types on
    import; task-lifecycle payloads must be registered by their owning service
    (idempotent: re-registering the same model for a key is a no-op).
    """
    _register_schema("task.created", "1.1.0", TaskCreatedPayload)
    _register_schema("task.planning.started", "1.0.0", TaskPlanningStartedPayload)
    _register_schema("task.execution.started", "1.0.0", TaskExecutionStartedPayload)
    _register_schema("task.step.completed", "1.0.0", TaskStepCompletedPayload)
    _register_schema("task.completed", "1.0.0", TaskCompletedPayload)


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


@pytest_asyncio.fixture
async def materialized_trace(tmp_path: Path) -> AsyncGenerator[dict[str, object], None]:
    """Emit a realistic multi-source causal chain and project it to SQLite.

    Returns a dict carrying the shared ``trace_id``, the negative-control
    ``other_trace_id``, the ``events_dir`` and ``db_url`` so the test can
    build the registry-api app and query ``GET /v1/trace``.
    """
    _register_event_types()

    rng = Random(7)
    clk = _FROZEN_CLOCK

    task_id = new_task_id(clock=clk, rng=rng)
    session_id = new_session_id(clock=clk, rng=rng)
    # ONE trace_id threaded across every in-trace event (UUIDv7).
    trace_id = new_uuid7(clock=clk, rng=rng)
    # A DIFFERENT trace_id for the negative-control event.
    other_trace_id = new_uuid7(clock=clk, rng=rng)

    # Distinct actor identities stand for distinct service code paths. These
    # are the SAME ``actor`` values the real services stamp on their
    # envelopes; the /trace response surfaces them as ``actor.id``.
    api_actor = Actor(kind="operator", id="registry-api")
    worker_actor = Actor(kind="system", id="worker-wrapper")
    orchestrator_actor = Actor(kind="system", id="orchestrator-adapter")

    def _in_trace(
        *,
        event_type: str,
        schema_version: str,
        actor: Actor,
        payload: object,
        mono_ns: int,
    ) -> EventEnvelope:
        return EventEnvelope.create(
            event_id=new_event_id(clock=clk, rng=rng),
            schema_version=schema_version,
            type=event_type,
            emitted_at=clk.now(),
            emitted_at_monotonic_ns=mono_ns,
            actor=actor,
            payload=payload,  # type: ignore[arg-type]
            trace_id=trace_id,
            request_id=new_uuid7(clock=clk, rng=rng),
        )

    # Realistic Journey-1 causal chain across ≥3 distinct service code paths,
    # all carrying the SAME trace_id (mono_ns ascending = causal order):
    #   1. task.created          — registry-api operator ingress
    #   2. task.planning.started — worker-wrapper
    #   3. task.execution.started— orchestrator-adapter
    #   4. task.step.completed   — worker-wrapper
    #   5. task.completed        — worker-wrapper
    in_trace_chain = [
        _in_trace(
            event_type="task.created",
            schema_version="1.1.0",
            actor=api_actor,
            payload=TaskCreatedPayload(task_id=task_id, title="cross-service-trace"),
            mono_ns=1000,
        ),
        _in_trace(
            event_type="task.planning.started",
            schema_version="1.0.0",
            actor=worker_actor,
            payload=TaskPlanningStartedPayload(task_id=task_id),
            mono_ns=2000,
        ),
        _in_trace(
            event_type="task.execution.started",
            schema_version="1.0.0",
            actor=orchestrator_actor,
            payload=TaskExecutionStartedPayload(task_id=task_id, session_id=session_id),
            mono_ns=3000,
        ),
        _in_trace(
            event_type="task.step.completed",
            schema_version="1.0.0",
            actor=worker_actor,
            payload=TaskStepCompletedPayload(
                task_id=task_id,
                step=1,
                description="apply the change",
                output_summary="done",
            ),
            mono_ns=4000,
        ),
        _in_trace(
            event_type="task.completed",
            schema_version="1.0.0",
            actor=worker_actor,
            payload=TaskCompletedPayload(task_id=task_id, summary="all done"),
            mono_ns=5000,
        ),
    ]

    # Negative control: a DIFFERENT trace_id on its own task — must NOT appear
    # in the trace_id=trace_id query result.
    negative_control = EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.1.0",
        type="task.created",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=9000,
        actor=api_actor,
        payload=TaskCreatedPayload(task_id=new_task_id(clock=clk, rng=rng), title="unrelated"),
        trace_id=other_trace_id,
        request_id=new_uuid7(clock=clk, rng=rng),
    )

    events_dir = tmp_path / "events"
    db_url = _db_url(tmp_path / "state.sqlite3")

    # Build the shared registry-state SQLite schema.
    engine = create_engine(db_url)
    from registry_state.schema import Base  # noqa: IMP001 — schema seeding only

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # ── Real on-disk emission path ──────────────────────────────────────
    # Every service writes to the SHARED daily JSONL log via EventLogWriter.
    # Interleave the negative control between in-trace events to prove the
    # query filters by trace_id, not by file position.
    writer = EventLogWriter(base_dir=events_dir, clock=clk)
    await writer.append(in_trace_chain[0])
    await writer.append(negative_control)
    for envelope in in_trace_chain[1:]:
        await writer.append(envelope)
    await writer.close()

    # ── Real read-back + canonical projection ───────────────────────────
    # Read the JSONL log with the production reader and project it into the
    # events table via the real Materializer + default handlers (this is the
    # code path that wires envelope.trace_id → events.trace_id).
    read_envelopes: list[EventEnvelope] = []
    for path in sorted(events_dir.glob("*.jsonl")):
        read_envelopes.extend(read_log_lines(path))

    materializer = Materializer(session_maker=get_session(engine))
    register_default_handlers(materializer)
    applied = await materializer.apply_many(read_envelopes)
    assert applied == len(in_trace_chain) + 1, (
        f"materializer should project all 6 events; applied={applied}"
    )
    await engine.dispose()

    yield {
        "trace_id": trace_id,
        "other_trace_id": other_trace_id,
        "events_dir": events_dir,
        "db_url": db_url,
    }


@pytest_asyncio.fixture
async def client(
    materialized_trace: dict[str, object],
) -> AsyncGenerator[AsyncClient, None]:
    """In-process registry-api ASGI client over the materialized state."""
    app = build_app(
        base_dir=materialized_trace["events_dir"],  # type: ignore[arg-type]
        db_url=materialized_trace["db_url"],  # type: ignore[arg-type]
        clock=_FROZEN_CLOCK,
    )
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.mark.integration
@pytest.mark.asyncio
async def test_trace_id_threads_across_services_via_trace_query(
    client: AsyncClient,
    materialized_trace: dict[str, object],
) -> None:
    """NFR-O7 / FR59a: one trace_id query returns the COMPLETE cross-service chain.

    Asserts:
      * GET /v1/trace/{trace_id} returns EXACTLY the 5 in-trace events.
      * They are ordered by emitted_at_monotonic_ns ascending.
      * Every returned event carries the queried trace_id (non-null).
      * The events span MULTIPLE distinct service sources — registry-api,
        worker-wrapper AND orchestrator-adapter all appear (proves "across
        services").
      * The negative-control event (a DIFFERENT trace_id) is EXCLUDED.
    """
    trace_id: str = materialized_trace["trace_id"]  # type: ignore[assignment]
    other_trace_id: str = materialized_trace["other_trace_id"]  # type: ignore[assignment]

    response = await client.get(f"/v1/trace/{trace_id}")
    assert response.status_code == 200
    rows = response.json()

    # EXACTLY the 5 in-trace events (negative control excluded).
    assert len(rows) == 5, f"expected 5 in-trace events, got {len(rows)}: {rows!r}"

    # Complete causal chain, in mono_ns order.
    assert [r["type"] for r in rows] == [
        "task.created",
        "task.planning.started",
        "task.execution.started",
        "task.step.completed",
        "task.completed",
    ]
    mono_values = [r["emitted_at_monotonic_ns"] for r in rows]
    assert mono_values == sorted(mono_values), (
        f"results must be ascending by emitted_at_monotonic_ns; got {mono_values!r}"
    )

    # NFR-O7: every event in the chain carries a non-null trace_id == queried.
    assert all(r["trace_id"] == trace_id for r in rows)
    assert all(r["trace_id"] is not None for r in rows)

    # "Across services": the chain spans MULTIPLE distinct sources. Each
    # service stamps its own ``actor`` on its emissions; /trace surfaces it
    # as ``actor.id``. All three sources must be present.
    actor_ids = {r["actor"]["id"] for r in rows}
    assert actor_ids == {
        "registry-api",
        "worker-wrapper",
        "orchestrator-adapter",
    }, f"trace must span all 3 service sources; got {actor_ids!r}"

    # Negative control: the other trace_id never leaks into this result.
    returned_trace_ids = {r["trace_id"] for r in rows}
    assert other_trace_id not in returned_trace_ids


@pytest.mark.integration
@pytest.mark.asyncio
async def test_other_trace_id_returns_only_its_own_event(
    client: AsyncClient,
    materialized_trace: dict[str, object],
) -> None:
    """NFR-O7 isolation: querying the negative-control trace_id returns ONLY it.

    Proves the /trace filter partitions the shared event log strictly by
    trace_id — the 5 cross-service events for the primary trace_id do NOT
    bleed into the unrelated trace_id's chain.
    """
    other_trace_id: str = materialized_trace["other_trace_id"]  # type: ignore[assignment]
    trace_id: str = materialized_trace["trace_id"]  # type: ignore[assignment]

    response = await client.get(f"/v1/trace/{other_trace_id}")
    assert response.status_code == 200
    rows = response.json()

    assert len(rows) == 1, f"negative-control trace should have exactly 1 event: {rows!r}"
    assert rows[0]["trace_id"] == other_trace_id
    assert rows[0]["trace_id"] != trace_id
    assert rows[0]["type"] == "task.created"
