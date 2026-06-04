"""verification-mcp MCP server — build/test recipes over a sandboxed worktree (Epic 17 / FR74).

Exports:
    ``build_server(*, worktree_root, clock, actor_kind, actor_id, ...) -> FastMCP``
        Synchronous factory that creates the FastMCP server. Story 17.2 ships
        the SCAFFOLD: no verification tools are registered yet (``TIER_MAP`` is
        empty); the tools (``verification.run_build`` / ``verification.run_tests``)
        land in Stories 17.3 / 17.4.

Architecture notes:
    - The factory wires the clawhip-bridge audit-emission lifespan exactly as
      git-mcp / task-registry does: when ``clawhip_bridge_command`` is configured,
      a stdio MCP client to clawhip-bridge is spawned inside the FastMCP lifespan
      and held by an ``EmitterHolder`` (consumed by the tool decorators once tools
      land in 17.3). Startup is bounded by ``asyncio.wait_for(initialize(),
      _INIT_TIMEOUT)`` (G-FN-3 bounded init).
    - ``VerificationExecutor`` realpath-resolves the worktree root and exposes a
      ``_contains`` containment helper. Story 17.2 ships ONLY the containment
      logic (import-clean + unit-tested); the actual recipe subprocess spawn
      lands in Story 17.3.

Sandbox model (vs git-mcp):
    verification-mcp runs the project's build/test RECIPES (NOT ``git``). It has
    no repo-local ``.git/config`` RCE concern, so it does NOT carry git-mcp's
    per-invocation ``-c`` hardening shields (``core.fsmonitor`` / ``diff.external``
    / ``core.sshCommand``). The sandbox is exactly three load-bearing controls:
    (1) ``cwd`` pinned to the worktree root, (2) the child-env restricted to an
    explicit allowlist (NO secrets, no ``os.environ.copy``), and (3) a wall-clock
    timeout that kills+reaps a wedged run. The same realpath worktree-containment
    check (``_contains``) refuses any path that escapes the root.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from capabilities import Tier
from events.envelope import ActorKind  # noqa: IMP001 — packages/
from mcp.server.fastmcp import FastMCP

from verification_mcp.adapters.clawhip_client import ClawhipBridgeClient, EmitterHolder

if TYPE_CHECKING:
    from events.clock import Clock  # noqa: IMP001 — packages/

log = logging.getLogger(__name__)

# G-FN-3 bounded init — mirror of git-mcp / task-registry / clawhip_client
# ``_INIT_TIMEOUT``. Startup spawn of the clawhip-bridge subprocess must complete
# within this bound or ``__aenter__`` raises (fail-loud, OQ-4) instead of hanging
# forever.
_INIT_TIMEOUT: float = 30.0

# Wall-clock bound for any single verification RECIPE invocation (Story 17.3). A
# build/test recipe can wedge (a hung compiler, a test waiting on stdin, a daemon
# that never exits) and must NOT hang the request path — ``run_recipe`` kills+reaps
# on timeout and raises ``VerificationTimeout``. Build/test recipes run longer than
# a git read, so the default bound is generous; callers may override per-invocation.
_VERIFICATION_TIMEOUT: float = 600.0

# Env allowlist for the spawned recipe subprocess (Story 17.3, P0/security). Only
# these parent-env vars are forwarded to the build/test recipe; everything else
# (operator / platform secrets) is dropped — NO ``os.environ.copy``. Unlike a git
# read, a build/test recipe legitimately needs the language toolchain on ``PATH``
# and the interpreter-resolution vars (a Python test run resolves the venv via
# ``VIRTUAL_ENV`` / ``PYTHONPATH``); these are toolchain config, NOT credentials.
# NEVER add ``GITHUB_TOKEN`` / ``ANTHROPIC_API_KEY`` / any ``*_TOKEN`` / ``*_KEY``
# / ``*_SECRET`` — a verification run executes project code and must never see an
# operator secret it could exfiltrate.
_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Process basics + locale
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        # Temp directories (recipes write scratch / coverage files here)
        "TMPDIR",
        "TMP",
        "TEMP",
        # Python interpreter / venv resolution (a pytest recipe needs these)
        "PYTHONPATH",
        "PYTHONUNBUFFERED",
        "VIRTUAL_ENV",
    }
)


def _build_env() -> dict[str, str]:
    """Return the explicit, sandboxed child-env for the recipe subprocess.

    P0/security (Story 17.3): start from the parent env filtered to
    ``_ENV_ALLOWLIST`` (drops every secret) — NOT ``os.environ.copy``. A
    verification run executes project code, so the child must carry only the
    toolchain-config vars the recipe needs and nothing that could leak a
    credential. ``PYTHONUNBUFFERED`` is forced on so captured recipe output is
    streamed (not block-buffered) for deterministic log capture.
    """
    env = {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}
    env["PYTHONUNBUFFERED"] = "1"
    return env


class VerificationTimeout(RuntimeError):  # noqa: N818 — domain-named (no Error suffix); mirrors git-mcp GitTimeout / events.errors CapabilityDenied precedent
    """Raised when a single recipe invocation exceeds its wall-clock timeout (Story 17.3).

    The wedged subprocess is killed and reaped before this propagates so no
    zombie/leaked process survives the request. Sibling of git-mcp's
    ``GitTimeout``.
    """


@dataclass(frozen=True)
class VerificationResult:
    """Structured outcome of a single recipe invocation (Story 17.3).

    A non-zero ``returncode`` is NOT an exception — handlers surface it as a
    structured ``{"pass": false, ...}`` payload. Only a timeout
    (``VerificationTimeout``) or a spawn failure propagates as an exception.
    """

    returncode: int
    stdout: str
    stderr: str


# Story 17.2 scaffold — empty until the verification tools land in 17.3 / 17.4.
# Re-exported from ``handlers.tools`` so the canonical TIER_MAP lives in one place
# (mirrors git-mcp's handlers/tools.py shape). Tools register against it later.
TIER_MAP: dict[str, Tier] = {}


class VerificationExecutor:
    """Sandbox guard for verification recipes confined to a single worktree.

    Story 17.2 ships ONLY the containment logic. The ``worktree_root`` is
    realpath-resolved at construction so symlink components in the configured
    path cannot later be used to escape the sandbox. ``_contains`` answers
    whether a candidate path resolves to a location inside the root — the
    load-bearing check the verification tools (17.3) call before invoking any
    recipe subprocess. NO subprocess is spawned in this story.
    """

    def __init__(self, worktree_root: Path) -> None:
        # ``realpath`` (not ``resolve``) so a non-existent root still resolves
        # its existing symlink components deterministically — the tools in 17.3
        # validate existence separately. ``strict=False`` semantics match
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

    async def run_recipe(
        self,
        command: list[str],
        *,
        timeout: float = _VERIFICATION_TIMEOUT,
    ) -> VerificationResult:
        """Run *command* in the sandbox and return its result.

        The ONLY recipe spawn-site (Story 17.3). Security (every item load-bearing):

        - ``create_subprocess_exec`` (the ``_exec`` variant), NEVER ``_shell``:
          *command* is passed as discrete argv elements, so no shell re-parses a
          user-supplied token.
        - ``cwd`` is pinned to the worktree root — a recipe cannot run from
          outside the sandbox.
        - ``env`` is the explicit allowlist from ``_build_env`` (no secrets, no
          ``os.environ.copy``).
        - A wedged recipe is bounded by ``timeout``; on expiry it is killed+reaped
          and ``VerificationTimeout`` is raised (no leaked process).

        A non-zero exit is NOT raised — it is returned in
        ``VerificationResult.returncode`` so handlers can surface a structured
        pass/fail. Output is decoded with ``errors="replace"`` so binary/garbled
        bytes never crash the request.
        """
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.worktree_root),
            env=_build_env(),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise VerificationTimeout(
                f"verification recipe {command!r} exceeded {timeout}s timeout"
            ) from exc
        return VerificationResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"),
        )


def build_server(
    *,
    worktree_root: Path,
    clock: Clock,
    actor_kind: ActorKind,
    actor_id: str,
    clawhip_bridge_command: str | None = None,
    clawhip_bridge_args: list[str] | None = None,
    registry_events_dir: Path | None = None,
) -> FastMCP:
    """Build and return a configured ``FastMCP`` server instance.

    Story 17.2 SCAFFOLD: no verification tools are registered (``TIER_MAP`` is
    empty). The clawhip-bridge audit-emission lifespan is wired so the first
    verification tool added in 17.3 inherits the FR26 single-writer audit path
    for free.

    Args:
        worktree_root: Sandbox root for all verification recipes. Realpath-
            resolved inside ``VerificationExecutor``; the tools (17.3) refuse any
            path that does not satisfy ``VerificationExecutor._contains``.
        clock: Injected clock for deterministic testing (reserved for the tools'
            event emission in 17.4).
        actor_kind: One of ``operator|orchestrator|worker|system|clawhip``.
        actor_id: Non-empty string identifying the calling actor.
        clawhip_bridge_command: Command (e.g. ``python``) to spawn the
            clawhip-bridge MCP subprocess for ``capability.denied`` audit
            emission. When None, audit emission is disabled (test mode).
        clawhip_bridge_args: Args for the clawhip-bridge subprocess
            (e.g. ``["-m", "clawhip_bridge_mcp"]``).
        registry_events_dir: Base dir of the JSONL event log (reserved for the
            ``verification.*`` event emission in Story 17.4). The verification
            tools are Tier-2 (no approval gate), so this dir is not consulted for
            an approval lookup; it is threaded for forward-compat with the event
            surface and to keep the factory signature aligned with git-mcp.

    Returns:
        A ``FastMCP`` instance ready to ``mcp.run()`` on stdio.
    """
    # Constructed now so a malformed ``worktree_root`` fails fast at build time;
    # the verification tools in 17.3 will close over this instance.
    _executor = VerificationExecutor(worktree_root)

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
                "verification_mcp_clawhip_client_ready",
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

    mcp = FastMCP("verification", lifespan=lifespan_fn) if lifespan_fn else FastMCP("verification")

    # Story 17.3/17.4: register the Tier-2 verification tools. ``emitter_holder``
    # is forwarded so the audit ``capability.denied`` decorator wraps each handler
    # AND so the tools emit a typed ``verification.*`` event through the FR26
    # single-writer surface once 17.4 lands.
    from verification_mcp.handlers.tools import register_tools

    register_tools(
        mcp,
        _executor,
        actor_kind=actor_kind,
        actor_id=actor_id,
        emitter_holder=emitter_holder,
    )

    return mcp


__all__ = ["VerificationExecutor", "build_server"]
