"""memory-mcp MCP server — persistent cross-task knowledge store (Epic 18 / ADR-0012).

Exports:
    ``build_server(*, store_path, clock, actor_kind, actor_id, ...) -> FastMCP``
        Synchronous factory that creates the FastMCP server. Stories 18.1 / 18.2
        ship the SCAFFOLD: the :class:`~memory_mcp.store.MemoryStore` is
        constructed and threaded into ``register_tools``, but no memory tools are
        registered yet (``TIER_MAP`` is empty); the memory tools land in Stories
        18.3 / 18.4.

Architecture notes:
    - The factory wires the clawhip-bridge audit-emission lifespan exactly as
      git-mcp / task-registry does: when ``clawhip_bridge_command`` is configured,
      a stdio MCP client to clawhip-bridge is spawned inside the FastMCP lifespan
      and held by an ``EmitterHolder`` (consumed by the tool decorators once tools
      land in 18.4). Startup is bounded by ``asyncio.wait_for(initialize(),
      _INIT_TIMEOUT)`` (G-FN-3 bounded init).
    - The :class:`~memory_mcp.store.MemoryStore` owns a DEDICATED SQLite database
      (NEVER the registry DB — ADR-0012 store-isolation P3-I2) at ``store_path``,
      opened in WAL mode with the FTS5 schema initialised at construction.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING

from capabilities import Tier
from events.envelope import ActorKind  # noqa: IMP001 — packages/
from mcp.server.fastmcp import FastMCP

from memory_mcp.adapters.clawhip_client import ClawhipBridgeClient, EmitterHolder
from memory_mcp.store import MemoryStore

if TYPE_CHECKING:
    from events.clock import Clock  # noqa: IMP001 — packages/

log = logging.getLogger(__name__)

# G-FN-3 bounded init — mirror of task-registry / git-mcp ``_INIT_TIMEOUT``.
# Startup spawn of the clawhip-bridge subprocess must complete within this bound
# or ``__aenter__`` raises (fail-loud, OQ-4) instead of hanging forever.
_INIT_TIMEOUT: float = 30.0

# Stories 18.1-18.2 scaffold — empty until the memory tools land in 18.3 / 18.4.
# Re-exported from ``handlers.tools`` so the canonical TIER_MAP lives in one place
# (mirrors the git-mcp / task-registry handlers/tools.py shape). Tools register
# against it later.
TIER_MAP: dict[str, Tier] = {}


def build_server(
    *,
    store_path: Path,
    clock: Clock,
    actor_kind: ActorKind,
    actor_id: str,
    clawhip_bridge_command: str | None = None,
    clawhip_bridge_args: list[str] | None = None,
    registry_events_dir: Path | None = None,
) -> FastMCP:
    """Build and return a configured ``FastMCP`` server instance.

    Stories 18.1 / 18.2 SCAFFOLD: the :class:`~memory_mcp.store.MemoryStore` is
    constructed (so a malformed ``store_path`` / unwritable store dir fails fast
    at build time) and threaded into ``register_tools``, but no memory tools are
    registered (``TIER_MAP`` is empty). The clawhip-bridge audit-emission lifespan
    is wired so the first memory tool added in 18.4 inherits the FR26
    single-writer audit path for free.

    Args:
        store_path: Absolute path to the DEDICATED SQLite store DB. The
            :class:`MemoryStore` opens it in WAL mode and initialises the FTS5
            schema. NEVER the registry DB (ADR-0012 store-isolation P3-I2).
        clock: Injected clock for deterministic testing (reserved for the memory
            tools' event emission in 18.4).
        actor_kind: One of ``operator|orchestrator|worker|system|clawhip``.
        actor_id: Non-empty string identifying the calling actor.
        clawhip_bridge_command: Command (e.g. ``python``) to spawn the
            clawhip-bridge MCP subprocess for ``capability.denied`` audit
            emission. When None, audit emission is disabled (test mode).
        clawhip_bridge_args: Args for the clawhip-bridge subprocess
            (e.g. ``["-m", "clawhip_bridge_mcp"]``).
        registry_events_dir: Base dir of the JSONL event log (reserved for the
            memory tools' event emission in 18.4). Threaded for parity with the
            git-mcp factory; unused in the scaffold.

    Returns:
        A ``FastMCP`` instance ready to ``mcp.run()`` on stdio.
    """
    # Constructed now so a malformed ``store_path`` / unwritable store dir fails
    # fast at build time; the memory tools in 18.3 / 18.4 close over this store.
    store = MemoryStore(store_path)

    # Reserved for the memory tools' event emission (18.4); threaded for parity
    # with the git-mcp factory and referenced here so the scaffold is import-clean.
    _ = registry_events_dir

    emitter_holder: EmitterHolder | None
    lifespan_fn = None
    if clawhip_bridge_command is not None and clawhip_bridge_args is not None:
        emitter_holder = EmitterHolder()

        @contextlib.asynccontextmanager
        async def _lifespan(_server: FastMCP) -> AsyncGenerator[None, None]:
            """Spawn the clawhip-bridge stdio client; fail-loud on startup (OQ-4).

            Mirror of git-mcp / task-registry's lifespan. Startup failure
            (clawhip-bridge subprocess refuses to launch) propagates here so the
            operator sees a hard error instead of silently degrading to
            no-emission mode. The bounded ``asyncio.wait_for(initialize(),
            _INIT_TIMEOUT)`` lives inside ``ClawhipBridgeClient.__aenter__``
            (G-FN-3).
            """
            # Explicit RuntimeError, not assert — ``python -O`` strips asserts.
            if clawhip_bridge_command is None:
                raise RuntimeError("clawhip_bridge_command must be set when lifespan_fn is wired")
            if clawhip_bridge_args is None:
                raise RuntimeError("clawhip_bridge_args must be set when lifespan_fn is wired")
            if emitter_holder is None:
                raise RuntimeError("emitter_holder must be initialized when lifespan_fn is wired")
            client = ClawhipBridgeClient(
                command=clawhip_bridge_command,
                args=clawhip_bridge_args,
                caller_actor_kind=actor_kind,
                caller_actor_id=actor_id,
            )
            await client.__aenter__()
            emitter_holder.client = client
            log.info(
                "memory_mcp_clawhip_client_ready",
                extra={"command": clawhip_bridge_command, "args": clawhip_bridge_args},
            )
            try:
                yield
            finally:
                # Null the holder ONLY AFTER ``__aexit__`` completes so any
                # in-flight handler sees a closed-pipe error (PD-1 fail-soft),
                # not a "lifespan not wired" RuntimeError. Nested try/finally so
                # the reference is ALWAYS dropped even if teardown raises.
                try:
                    await client.__aexit__(None, None, None)
                finally:
                    emitter_holder.client = None

        lifespan_fn = _lifespan
    else:
        emitter_holder = None

    mcp = FastMCP("memory", lifespan=lifespan_fn) if lifespan_fn else FastMCP("memory")

    # Stories 18.1-18.2 scaffold: no tools registered yet (``TIER_MAP`` empty).
    # The store + emitter_holder are threaded so the memory tools (18.3 / 18.4)
    # close over a live store + the FR26 single-writer audit surface.
    from memory_mcp.handlers.tools import register_tools

    register_tools(
        mcp,
        store,
        actor_kind=actor_kind,
        actor_id=actor_id,
        emitter_holder=emitter_holder,
    )

    return mcp


__all__ = ["MemoryStore", "build_server"]
