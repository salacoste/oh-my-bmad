"""task-registry MCP server — read-only resources + bounded-write tools (Story 5.8).

Exports:
    ``build_server(*, db_path, actor_kind, actor_id, _session_maker=None) -> FastMCP``
        Factory that creates the FastMCP server with 4 resources + 3 tools
        registered. Inject configuration at startup; call ``mcp.run()`` to serve.

Architecture notes:
    - 4 resources are read-only — they query the materialized SQLite state via
      SQLAlchemy async ORM (select-only). The engine is opened with
      ``read_only=True`` so the OS rejects writes at the connection level.
    - 3 bounded-write tools are validated stubs (Phase 1). They check tier,
      validate inputs, and return success. Actual persistence routes through
      the event spine via clawhip-bridge — deferred to Story 5.12 integration.
    - Tier enforcement is a NO-OP placeholder; full tiers land in Stories 6.1-6.3.
"""

from __future__ import annotations

import atexit
import logging
from typing import TYPE_CHECKING

from events.envelope import ActorKind  # noqa: IMP001 — packages/
from mcp.server.fastmcp import FastMCP
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — mcp-servers→services allowed per AC-7/Arch
    create_engine,
    get_session,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

log = logging.getLogger(__name__)


def build_server(
    *,
    db_path: str = "",
    actor_kind: ActorKind,
    actor_id: str,
    _session_maker: async_sessionmaker | None = None,
) -> FastMCP:
    """Build and return a configured ``FastMCP`` server instance.

    Registers 4 read-only resources + 3 bounded-write tools.

    Args:
        db_path: Path to the SQLite database file (production).
        actor_kind: One of ``operator|orchestrator|worker|system|clawhip``.
        actor_id: Non-empty string identifying the calling actor.
        _session_maker: Override session maker (for testing with in-memory DB).

    Returns:
        A ``FastMCP`` instance ready to ``mcp.run()`` on stdio.
    """
    from task_registry_mcp.handlers.resources import register_resources
    from task_registry_mcp.handlers.tools import register_tools

    engine = None
    if _session_maker is not None:
        session_maker = _session_maker
    else:
        if not db_path:
            raise ValueError("db_path is required when _session_maker is not provided")
        db_url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_engine(db_url, read_only=True)
        session_maker = get_session(engine)
        atexit.register(engine.sync_engine.dispose)

    mcp = FastMCP("task-registry")

    register_resources(mcp, session_maker, actor_kind)
    register_tools(mcp, session_maker, actor_kind, actor_id)

    return mcp


__all__ = ["build_server"]
