"""github-mcp MCP server — bounded GitHub REST operations (Epic 16 / FR73).

Exports:
    ``build_server(*, actor_kind, actor_id, scoped_token, clock, ...) -> FastMCP``
        Synchronous factory that creates the FastMCP server. Story 16.2 ships
        the SCAFFOLD: no github tools are registered yet (``TIER_MAP`` is empty);
        the github tools land in Stories 16.3 / 16.4.

Architecture notes:
    - The factory wires the clawhip-bridge audit-emission lifespan exactly as
      git-mcp / task-registry do: when ``clawhip_bridge_command`` is configured,
      a stdio MCP client to clawhip-bridge is spawned inside the FastMCP lifespan
      and held by an ``EmitterHolder`` (consumed by the tool decorators once
      tools land in 16.3 / 16.4). Startup is bounded by
      ``asyncio.wait_for(initialize(), _INIT_TIMEOUT)`` (G-FN-3 bounded init).
    - ``scoped_token`` is the Story-16.5 narrowly-scoped GitHub credential the
      REST adapter authenticates with. Story 16.2 only threads it through to the
      tool registration seam; the actual ``aiohttp`` REST client lands in 16.3.
      The broad ``GITHUB_TOKEN`` is NEVER used (it is a forbidden secret).
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

from github_mcp.adapters.clawhip_client import ClawhipBridgeClient, EmitterHolder

if TYPE_CHECKING:
    from events.clock import Clock  # noqa: IMP001 — packages/

log = logging.getLogger(__name__)

# G-FN-3 bounded init — mirror of git-mcp / task-registry ``_INIT_TIMEOUT``.
# Startup spawn of the clawhip-bridge subprocess must complete within this bound
# or ``__aenter__`` raises (fail-loud, OQ-4) instead of hanging forever.
_INIT_TIMEOUT: float = 30.0

# Story 16.2 scaffold — empty until the github tools land in 16.3 / 16.4.
# Re-exported from ``handlers.tools`` so the canonical TIER_MAP lives in one
# place (mirrors git-mcp / task-registry handlers/tools.py shape). Tools register
# against it later.
TIER_MAP: dict[str, Tier] = {}


def build_server(
    *,
    actor_kind: ActorKind,
    actor_id: str,
    scoped_token: str,
    clock: Clock,
    clawhip_bridge_command: str | None = None,
    clawhip_bridge_args: list[str] | None = None,
    registry_events_dir: Path | None = None,
) -> FastMCP:
    """Build and return a configured ``FastMCP`` server instance.

    Story 16.2 SCAFFOLD: no github tools are registered (``TIER_MAP`` is empty).
    The clawhip-bridge audit-emission lifespan is wired so the first github tool
    added in 16.3 inherits the FR26 single-writer audit path for free.

    Args:
        actor_kind: One of ``operator|orchestrator|worker|system|clawhip``.
        actor_id: Non-empty string identifying the calling actor.
        scoped_token: The Story-16.5 narrowly-scoped GitHub credential the REST
            adapter (16.3 / 16.4) authenticates with. NEVER the broad
            ``GITHUB_TOKEN``. Closed over by the per-call ``GitHubReadClient``
            factory as its bearer — never returned in a tool result.
        clock: Injected clock for deterministic testing (reserved for the github
            tools' event emission and the Tier-3 approval lookup in 16.3 / 16.4).
        clawhip_bridge_command: Command (e.g. ``python``) to spawn the
            clawhip-bridge MCP subprocess for ``capability.denied`` audit
            emission. When None, audit emission is disabled (test mode).
        clawhip_bridge_args: Args for the clawhip-bridge subprocess
            (e.g. ``["-m", "clawhip_bridge_mcp"]``).
        registry_events_dir: Base dir of the JSONL event log, scanned by the
            Tier-3 ``approval_lookup`` (Story 16.4) for an ``approval.granted``
            matching the caller's ``task_id``. When None, the Tier-3 github tools
            have NO approval source and every Tier-3 call is denied
            (test/no-approval default).

    Returns:
        A ``FastMCP`` instance ready to ``mcp.run()`` on stdio.
    """
    # ``scoped_token`` authenticates the 16.3 read adapter. A fresh
    # ``GitHubReadClient`` is opened per tool call (an ``async with`` session) —
    # the simplest-correct lifecycle for the read scaffold (no long-lived session
    # state to share). The broad ``GITHUB_TOKEN`` is NEVER read.
    from github_mcp.adapters.github_rest import GitHubReadClient

    def _read_client_factory() -> GitHubReadClient:
        return GitHubReadClient(scoped_token=scoped_token)

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
                "github_mcp_clawhip_client_ready",
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

    mcp = FastMCP("github", lifespan=lifespan_fn) if lifespan_fn else FastMCP("github")

    # Story 16.3/16.4: register the read (Tier-1) + write (Tier-3) github tools.
    # ``clock`` is threaded so the Tier-3 ``approval_lookup`` scans TODAY's JSONL
    # event log (``current_day_path(events_dir, clock.now())``). ``emitter_holder``
    # is forwarded so the audit ``capability.denied`` decorator wraps each handler
    # AND so the write tools emit ``github.*`` events through the FR26
    # single-writer surface. ``registry_events_dir`` is the approval-source base
    # dir; when None, every Tier-3 call is denied (no approval source). Story 16.2
    # registers NO tools yet (empty TIER_MAP).
    from github_mcp.handlers.tools import make_approval_lookup, register_tools

    approval_lookup = (
        make_approval_lookup(registry_events_dir, clock)
        if registry_events_dir is not None
        else None
    )

    register_tools(
        mcp,
        _read_client_factory,
        actor_kind=actor_kind,
        actor_id=actor_id,
        emitter_holder=emitter_holder,
        approval_lookup=approval_lookup,
    )

    return mcp


__all__ = ["build_server"]
