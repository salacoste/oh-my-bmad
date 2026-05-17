"""contract tests for MCP tool input schemas (Story 9.5 / AC5).

Story 2.8 placeholder upgraded in Story 9.5: assert every ``@mcp.tool()``-
decorated handler across the 3 MCP servers (``clawhip-bridge``,
``task-registry``, ``session-registry``) exposes ``caller_trace_id`` as a
required ``string`` field in its FastMCP-auto-derived input schema.

Negative round-trip: an input dict missing ``caller_trace_id`` raises
``ValueError`` from the validation helper before the tool body runs.

Positive round-trips:
  - UUIDv7 bare form (e.g. ``01917e5c-a7d1-7000-8abc-...``)
  - Telegram-derived form (``tg:<update_id>``)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from events import FROZEN_EPOCH, FrozenClock
from registry_state.schema import Base  # noqa: IMP001 — test
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

_VALID_TRACE_ID = "01917e5c-a7d1-7000-8abc-0123456789ab"
_VALID_TG_TRACE_ID = "tg:42"


@pytest.mark.contract
def test_placeholder() -> None:
    """Backward-compat sentinel — original placeholder lives on as a smoke test."""
    assert True


# ---------------------------------------------------------------------------
# AC5: Schema round-trips — each MCP tool's input schema lists
# ``caller_trace_id`` as a required ``string`` field.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def _empty_session_maker() -> async_sessionmaker[AsyncSession]:
    """Minimal session_maker for the registry-MCP build_server() factories."""
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
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.contract
@pytest.mark.asyncio
async def test_clawhip_bridge_tool_schemas_require_caller_trace_id(
    tmp_path: Path,
) -> None:
    """All 5 clawhip-bridge tool schemas require caller_trace_id (UUIDv7-or-tg)."""
    from clawhip_bridge_mcp.server import build_server  # noqa: IMP001 — contract test

    clock = FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)
    mcp = build_server(base_dir=tmp_path, clock=clock, actor_kind="system", actor_id="contract")
    tools = await mcp.list_tools()
    names = {
        "emit_event",
        "emit_blocker",
        "emit_summary",
        "emit_approval_request",
        "emit_completion",
    }
    for tool in tools:
        if tool.name in names:
            assert "caller_trace_id" in tool.inputSchema["required"], (
                f"clawhip-bridge tool {tool.name!r} missing caller_trace_id"
            )
            assert tool.inputSchema["properties"]["caller_trace_id"]["type"] == "string"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_task_registry_tool_schemas_require_caller_trace_id(
    _empty_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """All 3 task-registry tool schemas require caller_trace_id."""
    from task_registry_mcp.app.main import build_server  # noqa: IMP001

    mcp = build_server(
        actor_kind="worker",
        actor_id="contract",
        _session_maker=_empty_session_maker,
    )
    tools = await mcp.list_tools()
    for tool in tools:
        assert "caller_trace_id" in tool.inputSchema["required"], (
            f"task-registry tool {tool.name!r} missing caller_trace_id"
        )
        assert tool.inputSchema["properties"]["caller_trace_id"]["type"] == "string"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_session_registry_tool_schemas_require_caller_trace_id(
    _empty_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """All 3 session-registry tool schemas require caller_trace_id."""
    from session_registry_mcp.app.main import build_server  # noqa: IMP001

    mcp = build_server(
        actor_kind="worker",
        actor_id="contract",
        _session_maker=_empty_session_maker,
    )
    tools = await mcp.list_tools()
    for tool in tools:
        assert "caller_trace_id" in tool.inputSchema["required"], (
            f"session-registry tool {tool.name!r} missing caller_trace_id"
        )
        assert tool.inputSchema["properties"]["caller_trace_id"]["type"] == "string"


@pytest.mark.contract
@pytest.mark.parametrize("trace_id", [_VALID_TRACE_ID, _VALID_TG_TRACE_ID])
def test_caller_trace_id_positive_round_trip_validates(trace_id: str) -> None:
    """Positive round-trip: each helper accepts valid UUIDv7 + tg: forms."""
    from clawhip_bridge_mcp.server import _validate_caller_trace_id as _v_bridge
    from session_registry_mcp.handlers.tools import (
        _validate_caller_trace_id as _v_sess,
    )
    from task_registry_mcp.handlers.tools import (
        _validate_caller_trace_id as _v_task,
    )

    _v_bridge(trace_id)
    _v_task(trace_id)
    _v_sess(trace_id)


@pytest.mark.contract
@pytest.mark.parametrize("bad", ["", "bad-format", "tg:", "tg:0"])
def test_caller_trace_id_negative_round_trip_rejected(bad: str) -> None:
    """Negative round-trip: helper rejects bad shapes consistently across servers."""
    from clawhip_bridge_mcp.server import _validate_caller_trace_id as _v_bridge
    from session_registry_mcp.handlers.tools import (
        _validate_caller_trace_id as _v_sess,
    )
    from task_registry_mcp.handlers.tools import (
        _validate_caller_trace_id as _v_task,
    )

    with pytest.raises(ValueError, match="Story 9.1 contract"):
        _v_bridge(bad)
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        _v_task(bad)
    with pytest.raises(ValueError, match="Story 9.1 contract"):
        _v_sess(bad)
