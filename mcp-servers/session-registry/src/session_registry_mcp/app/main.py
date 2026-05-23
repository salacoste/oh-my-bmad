"""session-registry MCP server — read-only resources + bounded-write tools (Story 5.9).

Exports:
    ``build_server(*, db_path, actor_kind, actor_id, _session_maker=None) -> FastMCP``
        Factory that creates the FastMCP server with 3 resources + 3 tools
        registered. Inject configuration at startup; call ``mcp.run()`` to serve.

Architecture notes:
    - 3 resources are read-only — they query the materialized SQLite state via
      SQLAlchemy async ORM (select-only). The engine is opened with
      ``read_only=True`` so the OS rejects writes at the connection level.
    - 3 bounded-write tools are validated stubs (Phase 1). They check tier,
      validate inputs, and return success. Actual persistence routes through
      the event spine via clawhip-bridge — deferred to Story 5.12 integration.
    - Tier enforcement is a NO-OP placeholder; full tiers land in Stories 6.1-6.3.

Story 11.2.2: when *clawhip_bridge_command* is provided, a stdio MCP client
to clawhip-bridge is spawned inside the FastMCP lifespan and wired into an
``EmitterHolder`` that the tool decorators consume. ``CapabilityDenied``
raised from ``check_tier`` then emits a ``capability.denied`` audit envelope
via the FR26-compliant single-writer surface before re-raising.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from events.envelope import ActorKind  # noqa: IMP001 — packages/
from mcp.server.fastmcp import FastMCP
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — mcp-servers→services allowed per AC-7/Arch
    create_engine,
    get_session,
)

from session_registry_mcp.adapters.clawhip_client import ClawhipBridgeClient, EmitterHolder

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

log = logging.getLogger(__name__)


def build_server(
    *,
    db_path: str = "",
    actor_kind: ActorKind,
    actor_id: str,
    _session_maker: async_sessionmaker | None = None,
    clawhip_bridge_command: str | None = None,
    clawhip_bridge_args: list[str] | None = None,
) -> FastMCP:
    """Build and return a configured ``FastMCP`` server instance.

    Registers 3 read-only resources + 3 bounded-write tools.

    Args:
        db_path: Path to the SQLite database file (production).
        actor_kind: One of ``operator|orchestrator|worker|system|clawhip``.
        actor_id: Non-empty string identifying the calling actor.
        _session_maker: Override session maker (for testing with in-memory DB).
        clawhip_bridge_command: Story 11.2.2 — command (e.g. ``python``) to
            spawn the clawhip-bridge MCP subprocess for ``capability.denied``
            audit emission. When None, audit emission is disabled (test mode).
        clawhip_bridge_args: Story 11.2.2 — args for the clawhip-bridge
            subprocess (e.g. ``["-m", "clawhip_bridge_mcp"]``).

    Returns:
        A ``FastMCP`` instance ready to ``mcp.run()`` on stdio.
    """
    from session_registry_mcp.handlers.resources import register_resources
    from session_registry_mcp.handlers.tools import register_tools

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

    emitter_holder: EmitterHolder | None
    lifespan_fn = None
    if clawhip_bridge_command is not None and clawhip_bridge_args is not None:
        emitter_holder = EmitterHolder()

        @contextlib.asynccontextmanager
        async def _lifespan(_server: FastMCP) -> AsyncGenerator[None, None]:
            """Spawn the clawhip-bridge stdio client; fail-loud on startup (OQ-4)."""
            assert clawhip_bridge_command is not None  # mypy narrowing
            assert clawhip_bridge_args is not None
            assert emitter_holder is not None
            client = ClawhipBridgeClient(
                command=clawhip_bridge_command,
                args=clawhip_bridge_args,
            )
            await client.__aenter__()
            emitter_holder.client = client
            log.info(
                "session_registry_clawhip_client_ready",
                extra={"command": clawhip_bridge_command, "args": clawhip_bridge_args},
            )
            try:
                yield
            finally:
                emitter_holder.client = None
                await client.__aexit__(None, None, None)

        lifespan_fn = _lifespan
    else:
        emitter_holder = None

    mcp = (
        FastMCP("session-registry", lifespan=lifespan_fn)
        if lifespan_fn
        else FastMCP("session-registry")
    )

    register_resources(mcp, session_maker, actor_kind)
    register_tools(mcp, session_maker, actor_kind, actor_id, emitter_holder=emitter_holder)

    log.info("session-registry server built: actor_kind=%s actor_id=%s", actor_kind, actor_id)
    return mcp


__all__ = ["build_server"]
