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

import logging
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — mcp-servers→services allowed per AC-7/Arch
    create_engine,
    get_session,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

log = logging.getLogger(__name__)


def _check_tier(actor_kind: str, tool_name: str) -> bool:
    """NO-OP capability-tier gate (Phase 1 placeholder).

    Story 6.1-6.3 replaces this with real Tier 0/1/2/3 enforcement.
    """
    log.debug(
        "tier-check (no-op): actor_kind=%s tool=%s — full enforcement in Story 6.1",
        actor_kind,
        tool_name,
    )
    return True


def build_server(
    *,
    db_path: str = "",
    actor_kind: str,
    actor_id: str,
    _session_maker: async_sessionmaker | None = None,
) -> FastMCP:
    """Build and return a configured ``FastMCP`` server instance.

    Registers 4 read-only resources + 3 bounded-write tools.

    Args:
        db_path: Path to the SQLite database file (production).
        actor_kind: One of ``operator|orchestrator|worker|system``.
        actor_id: Non-empty string identifying the calling actor.
        _session_maker: Override session maker (for testing with in-memory DB).

    Returns:
        A ``FastMCP`` instance ready to ``mcp.run()`` on stdio.
    """
    from task_registry_mcp.handlers.resources import register_resources
    from task_registry_mcp.handlers.tools import register_tools

    if _session_maker is not None:
        session_maker = _session_maker
    else:
        db_url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_engine(db_url, read_only=True)
        session_maker = get_session(engine)

    mcp = FastMCP("task-registry")

    register_resources(mcp, session_maker, actor_kind)
    register_tools(mcp, session_maker, actor_kind, actor_id)

    return mcp


__all__ = ["build_server"]
