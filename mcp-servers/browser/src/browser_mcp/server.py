"""browser-mcp MCP server — browser automation via Playwright MCP subprocess (Epic 20 / FR78).

Exports:
    ``build_server(*, clock, actor_kind, actor_id, playwright_image, ...) -> FastMCP``
        Synchronous factory that creates the FastMCP server. Story 20.1 ships
        the SCAFFOLD: no browser tools are registered yet (``TIER_MAP`` is empty);
        the browser tools land in Stories 21.1-21.5.

Architecture notes:
    - The factory wires the clawhip-bridge audit-emission lifespan exactly as
      task-registry / git-mcp do: when ``clawhip_bridge_command`` is configured,
      a stdio MCP client to clawhip-bridge is spawned inside the FastMCP lifespan
      and held by an ``EmitterHolder`` (consumed by the tool decorators once tools
      land in 21.1). Startup is bounded by ``asyncio.wait_for(initialize(),
      _INIT_TIMEOUT)`` (G-FN-3 bounded init).
    - The Playwright MCP subprocess lifecycle management lands in Story 20.2.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING

from capabilities import Tier  # noqa: F401 — re-exported for check_tier_declarations
from events.envelope import ActorKind  # noqa: IMP001 — packages/
from mcp.server.fastmcp import FastMCP

from browser_mcp.adapters.clawhip_client import ClawhipBridgeClient, EmitterHolder
from browser_mcp.adapters.playwright_subprocess import PlaywrightSubprocessManager

if TYPE_CHECKING:
    from events.clock import Clock  # noqa: IMP001 — packages/

log = logging.getLogger(__name__)

_INIT_TIMEOUT: float = 30.0

# P4-I1/P4-I3 — caps that are NEVER allowed in BROWSER_MCP_EXTRA_CAPS.
_blocklisted_caps: frozenset[str] = frozenset({"storage", "network"})

# TIER_MAP is defined in handlers/tools.py (the canonical location) and
# re-exported at the bottom of this module for check_tier_declarations.py.
# Do NOT redefine it here.

# Child env allowlist for the browser server (Story 20.6 fills this out fully).
# Only these parent-env vars are forwarded; everything else is dropped.
# NEVER add GITHUB_TOKEN / ANTHROPIC_API_KEY / any *_TOKEN / *_KEY / *_SECRET.
_BROWSER_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TMP",
        "TEMP",
        # Browser-specific vars (added in Story 20.6, but scaffolded here)
        "BROWSER_MCP_ACTOR_KIND",
        "BROWSER_MCP_ACTOR_ID",
        "BROWSER_MCP_PLAYWRIGHT_IMAGE",
        "BROWSER_MCP_ALLOWED_HOSTS",
        "BROWSER_MCP_ALLOWED_ORIGINS",
        "BROWSER_MCP_EXTRA_CAPS",
    }
)


def build_server(
    *,
    clock: Clock,
    actor_kind: ActorKind,
    actor_id: str,
    playwright_image: str,
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
    extra_caps: list[str] | None = None,
    memory_limit: str | None = None,
    cpu_limit: float | None = None,
    clawhip_bridge_command: str | None = None,
    clawhip_bridge_args: list[str] | None = None,
    registry_events_dir: Path | None = None,
) -> FastMCP:
    """Build and return a configured FastMCP server instance.

    Story 20.1 SCAFFOLD: no browser tools are registered (TIER_MAP is empty).
    The clawhip-bridge audit-emission lifespan is wired so the first browser tool
    added in 21.1 inherits the FR26 single-writer audit path for free.

    Args:
        clock: Injected clock for deterministic testing (reserved for the
            browser tools' event emission in 21.1-21.5).
        actor_kind: One of ``operator|orchestrator|worker|system|clawhip``.
        actor_id: Non-empty string identifying the calling actor.
        playwright_image: Pinned Docker image digest for the Playwright MCP
            subprocess (e.g. ``mcr.microsoft.com/playwright/mcp@sha256:...``).
        allowed_hosts: Comma-separated host allowlist for browser navigation.
        allowed_origins: Comma-separated origin allowlist for browser navigation.
        extra_caps: Additional Playwright capabilities (blocklist enforced).
        clawhip_bridge_command: Command (e.g. ``python``) to spawn the
            clawhip-bridge MCP subprocess for ``capability.denied`` audit
            emission. When None, audit emission is disabled (test mode).
        clawhip_bridge_args: Args for the clawhip-bridge subprocess
            (e.g. ``["-m", "clawhip_bridge_mcp"]``).
        registry_events_dir: Base dir of the JSONL event log, scanned by the
            Tier-3 ``approval_lookup`` for an ``approval.granted`` matching
            the caller's ``task_id``. When None, Tier-3 calls are denied.

    Returns:
        A ``FastMCP`` instance ready to ``mcp.run()`` on stdio.
    """
    # Validate blocklisted caps (P4-I1/P4-I3)
    if extra_caps:
        blocked = _blocklisted_caps.intersection(extra_caps)
        if blocked:
            raise RuntimeError(
                f"BROWSER_MCP_EXTRA_CAPS contains blocklisted caps: {sorted(blocked)}. "
                f"storage and network are never allowed (P4-I1/P4-I3)."
            )

    emitter_holder: EmitterHolder | None
    lifespan_fn = None

    # Story 20.2 — Playwright subprocess manager. Created eagerly so tool
    # handlers can call ``manager.get_or_spawn(task_id)`` at request time.
    pw_manager = PlaywrightSubprocessManager(
        image=playwright_image,
        memory_limit=memory_limit or "512m",
        cpu_limit=cpu_limit or 1.0,
        extra_caps=extra_caps,
        allowed_origins=allowed_origins,
        allowed_hosts=allowed_hosts,
    )

    if clawhip_bridge_command is not None and clawhip_bridge_args is not None:
        emitter_holder = EmitterHolder()

        @contextlib.asynccontextmanager
        async def _lifespan(_server: FastMCP) -> AsyncGenerator[None, None]:
            """Spawn the clawhip-bridge stdio client; fail-loud on startup (OQ-4).

            Mirror of task-registry's / git-mcp's lifespan. Startup failure
            (clawhip-bridge subprocess refuses to launch) propagates here so
            the operator sees a hard error instead of silently degrading to
            no-emission mode. The bounded ``asyncio.wait_for(initialize(),
            _INIT_TIMEOUT)`` lives inside ``ClawhipBridgeClient.__aenter__``
            (G-FN-3).
            """
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
                "browser_mcp_clawhip_client_ready",
                extra={"command": clawhip_bridge_command},
            )
            try:
                yield
            finally:
                try:
                    await client.__aexit__(None, None, None)
                finally:
                    emitter_holder.client = None
                # NFR-R9: kill all orphaned Playwright subprocesses on shutdown.
                await pw_manager.kill_all()

        lifespan_fn = _lifespan
    else:
        emitter_holder = None

        @contextlib.asynccontextmanager
        async def _lifespan_no_emit(_server: FastMCP) -> AsyncGenerator[None, None]:
            """Minimal lifespan for test/no-audit mode — still kills Playwright on exit."""
            try:
                yield
            finally:
                await pw_manager.kill_all()

        lifespan_fn = _lifespan_no_emit

    mcp = FastMCP("browser", lifespan=lifespan_fn) if lifespan_fn else FastMCP("browser")

    # Story 21.1-21.5: register browser tools.
    # clock, emitter_holder, playwright_image, etc. are threaded through.
    from browser_mcp.handlers.tools import register_tools

    register_tools(
        mcp,
        actor_kind=actor_kind,
        actor_id=actor_id,
        emitter_holder=emitter_holder,
        pw_manager=pw_manager,
        allowed_hosts=allowed_hosts,
    )

    return mcp


__all__ = [
    "build_server",
    "TIER_MAP",
    "_BROWSER_ENV_ALLOWLIST",
]


# Re-export TIER_MAP from the canonical location (handlers/tools.py) so
# ``scripts/check_tier_declarations.py`` can import it from the server module.
# This also ensures the TIER_MAP is populated at import time (register_tools
# is called inside build_server, but TIER_MAP is a module-level constant).
from browser_mcp.handlers.tools import (  # noqa: F401, E402
    TIER_MAP as TIER_MAP,  # re-export
)
from browser_mcp.handlers.tools import (  # noqa: F401, E402
    validate_caller_trace_id,
)
