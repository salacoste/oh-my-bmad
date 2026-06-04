"""git-mcp MCP server — bounded git operations over a sandboxed worktree (Epic 15 / FR72).

Exports:
    ``build_server(*, worktree_root, clock, actor_kind, actor_id, ...) -> FastMCP``
        Synchronous factory that creates the FastMCP server. Story 15.2 ships
        the SCAFFOLD: no git tools are registered yet (``TIER_MAP`` is empty);
        the git tools land in Stories 15.3 / 15.4.

Architecture notes:
    - The factory wires the clawhip-bridge audit-emission lifespan exactly as
      task-registry does: when ``clawhip_bridge_command`` is configured, a stdio
      MCP client to clawhip-bridge is spawned inside the FastMCP lifespan and
      held by an ``EmitterHolder`` (consumed by the tool decorators once tools
      land in 15.3). Startup is bounded by ``asyncio.wait_for(initialize(),
      _INIT_TIMEOUT)`` (G-FN-3 bounded init).
    - ``GitExecutor`` realpath-resolves the worktree root and exposes a
      ``_contains`` containment helper. Story 15.2 ships ONLY the containment
      logic (import-clean + unit-tested); the actual ``git`` subprocess spawn
      lands in Story 15.3.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING

from capabilities import Tier
from events.envelope import ActorKind  # noqa: IMP001 — packages/
from mcp.server.fastmcp import FastMCP

from git_mcp.adapters.clawhip_client import ClawhipBridgeClient, EmitterHolder

if TYPE_CHECKING:
    from events.clock import Clock  # noqa: IMP001 — packages/

log = logging.getLogger(__name__)

# G-FN-3 bounded init — mirror of task-registry / clawhip_client ``_INIT_TIMEOUT``.
# Startup spawn of the clawhip-bridge subprocess must complete within this bound
# or ``__aenter__`` raises (fail-loud, OQ-4) instead of hanging forever.
_INIT_TIMEOUT: float = 30.0

# Story 15.2 scaffold — empty until the git tools land in 15.3 / 15.4. Re-exported
# from ``handlers.tools`` so the canonical TIER_MAP lives in one place (mirrors
# the task-registry handlers/tools.py shape). Tools register against it later.
TIER_MAP: dict[str, Tier] = {}


class GitExecutor:
    """Sandbox guard for git operations confined to a single worktree.

    Story 15.2 ships ONLY the containment logic. The ``worktree_root`` is
    realpath-resolved at construction so symlink components in the configured
    path cannot later be used to escape the sandbox. ``_contains`` answers
    whether a candidate path resolves to a location inside the root — the
    load-bearing check the git tools (15.3 / 15.4) call before invoking any
    ``git`` subprocess. NO subprocess is spawned in this story.
    """

    def __init__(self, worktree_root: Path) -> None:
        # ``realpath`` (not ``resolve``) so a non-existent root still resolves
        # its existing symlink components deterministically — the git tools in
        # 15.3 validate existence separately. ``strict=False`` semantics match
        # ``os.path.realpath`` for not-yet-created subpaths.
        self.worktree_root: Path = Path(os.path.realpath(worktree_root))

    def _contains(self, path: Path) -> bool:
        """Return True iff *path* resolves to a location inside the worktree root.

        Realpath-resolves *path* (collapsing ``..`` and symlinks) then checks
        containment via ``Path.is_relative_to``. The root itself counts as
        contained. An escaping path (``..`` traversal, absolute path outside
        the root, or a symlink pointing out) returns False.
        """
        resolved = Path(os.path.realpath(path))
        return resolved == self.worktree_root or resolved.is_relative_to(self.worktree_root)


def build_server(
    *,
    worktree_root: Path,
    clock: Clock,
    actor_kind: ActorKind,
    actor_id: str,
    clawhip_bridge_command: str | None = None,
    clawhip_bridge_args: list[str] | None = None,
) -> FastMCP:
    """Build and return a configured ``FastMCP`` server instance.

    Story 15.2 SCAFFOLD: no git tools are registered (``TIER_MAP`` is empty).
    The clawhip-bridge audit-emission lifespan is wired so the first git tool
    added in 15.3 inherits the FR26 single-writer audit path for free.

    Args:
        worktree_root: Sandbox root for all git operations. Realpath-resolved
            inside ``GitExecutor``; the git tools (15.3) refuse any path that
            does not satisfy ``GitExecutor._contains``.
        clock: Injected clock for deterministic testing (reserved for the git
            tools' event emission in 15.3 / 15.4).
        actor_kind: One of ``operator|orchestrator|worker|system|clawhip``.
        actor_id: Non-empty string identifying the calling actor.
        clawhip_bridge_command: Command (e.g. ``python``) to spawn the
            clawhip-bridge MCP subprocess for ``capability.denied`` audit
            emission. When None, audit emission is disabled (test mode).
        clawhip_bridge_args: Args for the clawhip-bridge subprocess
            (e.g. ``["-m", "clawhip_bridge_mcp"]``).

    Returns:
        A ``FastMCP`` instance ready to ``mcp.run()`` on stdio.
    """
    # Constructed now so a malformed ``worktree_root`` fails fast at build time;
    # the git tools in 15.3 will close over this instance.
    _executor = GitExecutor(worktree_root)

    emitter_holder: EmitterHolder | None
    lifespan_fn = None
    if clawhip_bridge_command is not None and clawhip_bridge_args is not None:
        emitter_holder = EmitterHolder()

        @contextlib.asynccontextmanager
        async def _lifespan(_server: FastMCP) -> AsyncGenerator[None, None]:
            """Spawn the clawhip-bridge stdio client; fail-loud on startup (OQ-4).

            Mirror of task-registry's lifespan. Startup failure (clawhip-bridge
            subprocess refuses to launch) propagates here so the operator sees a
            hard error instead of silently degrading to no-emission mode. The
            bounded ``asyncio.wait_for(initialize(), _INIT_TIMEOUT)`` lives
            inside ``ClawhipBridgeClient.__aenter__`` (G-FN-3).
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
                "git_mcp_clawhip_client_ready",
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

    mcp = FastMCP("git", lifespan=lifespan_fn) if lifespan_fn else FastMCP("git")

    # Story 15.2 scaffold: no tools registered yet. The git tools (15.3 / 15.4)
    # will call ``register_tools(mcp, _executor, ..., emitter_holder=emitter_holder)``.
    _ = (TIER_MAP, emitter_holder)

    return mcp


__all__ = ["GitExecutor", "build_server"]
