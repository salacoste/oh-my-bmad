"""Integration test: per-task budget policy inheritance (Story 12.4 / FR68a, AC7).

Exercises the full per-task token-ceiling data path IN-PROCESS, end to end:

    POST body (CreateTaskRequest)
      → TaskCreatedPayload (additive 1.2.0)
        → handle_task_created materializer → Task row (budget columns)
          → task-registry _task_to_dict (task://list serialization)
            → orchestrator-adapter _resolve_budget_limit (precedence)

Two scenarios per AC7:

* (a) task submitted WITHOUT budget fields → Task row stores NULL →
  ``_resolve_budget_limit`` falls through to the ``.env`` default
  (``OMB_DEFAULT_TASK_BUDGET_TOKENS``), and below that to the legacy
  ``ORCHESTRATOR_TASK_TOKEN_BUDGET`` — the inherited default ceiling.
* (b) task submitted WITH an explicit ``budget_token_limit`` → the Task row
  stores that value → ``_resolve_budget_limit`` returns the per-task value,
  overriding the default end-to-end.

Per Epic 11 retro L6 (test-fixture realism): uses the real
:class:`EventEnvelope.create` + the real ``handle_task_created`` materializer +
a real in-memory SQLite engine + the real ``_task_to_dict`` /
``_resolve_budget_limit`` production functions — NO hand-rolled envelopes and
NO re-implementation of the precedence logic under test.

``@pytest.mark.integration`` — excluded from the PR-gate ``just test`` run.
No external services are started.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from random import Random

import pytest
import pytest_asyncio
from events import (
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    FrozenClock,
    new_event_id,
    new_task_id,
    new_uuid7,
)
from events.payloads import TaskCreatedPayload
from orchestrator_adapter.app.config import OrchestratorSettings
from orchestrator_adapter.app.main import _resolve_budget_limit
from registry_state.adapters.sqlite_store import get_session
from registry_state.domain.handlers import handle_task_created
from registry_state.schema import Base, Task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from task_registry_mcp.handlers.resources import _task_to_dict

_ACTOR = Actor(kind="operator", id="integration-test")
_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000000"


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """In-memory SQLite session with schema created (mirrors registry-state's harness).

    StaticPool keeps every ``connect()`` on the same underlying sqlite3
    connection so the schema created on the begin-block is visible to the
    handler session.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = get_session(eng)
    async with sm() as session, session.begin():
        yield session
    await eng.dispose()


def _make_created_envelope(
    *,
    seed: int,
    budget_token_limit: int | None = None,
    budget_action: str | None = None,
) -> EventEnvelope:
    """Build a real ``task.created`` 1.2.0 envelope via the production factory.

    The payload carries the budget fields only when supplied; omitting them
    leaves both at their ``None`` default (the "inherit default" case).
    """
    rng = Random(seed)
    clk = FrozenClock(mono_ns=seed + 1, now=FROZEN_EPOCH)
    return EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version="1.2.0",
        type="task.created",
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=TaskCreatedPayload(
            task_id=new_task_id(clock=clk, rng=rng),
            title="budget-policy-inheritance",
            budget_token_limit=budget_token_limit,
            budget_action=budget_action,
        ),
        trace_id=_TRACE_ID,
        request_id=new_uuid7(clock=clk, rng=rng),
    )


async def _materialize_and_serialize(
    session: AsyncSession,
    envelope: EventEnvelope,
) -> dict[str, object]:
    """Run the materializer for *envelope* then return the ``_task_to_dict`` view.

    This is exactly the shape orchestrator-adapter sees from ``task://list``.
    """
    await handle_task_created(session, envelope)
    assert isinstance(envelope.payload, TaskCreatedPayload)
    row = (
        await session.execute(select(Task).where(Task.id == envelope.payload.task_id))
    ).scalar_one()
    return _task_to_dict(row)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_budget_policy_default_inherited_when_unspecified(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7(a): a task WITHOUT budget fields inherits the .env default ceiling.

    The Task row stores NULL for ``budget_token_limit``; ``_resolve_budget_limit``
    falls through to ``OMB_DEFAULT_TASK_BUDGET_TOKENS`` (here 77_000), proving the
    default is inherited rather than the legacy 50_000 fallback.
    """
    monkeypatch.setenv("OMB_DEFAULT_TASK_BUDGET_TOKENS", "77000")
    settings = OrchestratorSettings(_env_file=None)
    assert settings.default_task_budget_tokens == 77_000

    envelope = _make_created_envelope(seed=1)
    task_dict = await _materialize_and_serialize(db_session, envelope)

    # The row stored NULL (no per-task value) — surfaced as None.
    assert task_dict["budget_token_limit"] is None
    assert task_dict["budget_action"] is None

    # Precedence: no per-task value → OMB_DEFAULT_TASK_BUDGET_TOKENS.
    resolved = _resolve_budget_limit(task_dict, settings)
    assert resolved == 77_000


@pytest.mark.integration
@pytest.mark.asyncio
async def test_budget_policy_legacy_fallback_when_no_new_default(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7(a) corollary: with NEITHER per-task value NOR the new default set,
    the legacy ``ORCHESTRATOR_TASK_TOKEN_BUDGET`` (default 50_000) is used.

    Proves the full three-tier precedence chain bottoms out at the legacy var.
    """
    monkeypatch.delenv("OMB_DEFAULT_TASK_BUDGET_TOKENS", raising=False)
    settings = OrchestratorSettings(_env_file=None)
    assert settings.default_task_budget_tokens is None
    assert settings.task_token_budget == 50_000

    envelope = _make_created_envelope(seed=2)
    task_dict = await _materialize_and_serialize(db_session, envelope)
    assert task_dict["budget_token_limit"] is None

    resolved = _resolve_budget_limit(task_dict, settings)
    assert resolved == 50_000


@pytest.mark.integration
@pytest.mark.asyncio
async def test_budget_policy_explicit_overrides_default(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC7(b): an explicit per-task ``budget_token_limit`` overrides the default.

    The Task row stores the submitted ceiling; ``_resolve_budget_limit`` returns
    it even though ``OMB_DEFAULT_TASK_BUDGET_TOKENS`` is set to a different value
    — proving per-task > default end-to-end.
    """
    monkeypatch.setenv("OMB_DEFAULT_TASK_BUDGET_TOKENS", "77000")
    settings = OrchestratorSettings(_env_file=None)

    envelope = _make_created_envelope(
        seed=3,
        budget_token_limit=250_000,
        budget_action="failed",
    )
    task_dict = await _materialize_and_serialize(db_session, envelope)

    # The row stored the explicit per-task values.
    assert task_dict["budget_token_limit"] == 250_000
    assert task_dict["budget_action"] == "failed"

    # Precedence: per-task value wins over the default (77_000).
    resolved = _resolve_budget_limit(task_dict, settings)
    assert resolved == 250_000
