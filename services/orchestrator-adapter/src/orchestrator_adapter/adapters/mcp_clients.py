"""MCP client adapter — manages three stdio client connections (Story 5.10).

Same pattern as worker-wrapper ``adapters/mcp_clients.py`` (Story 5.1).
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

import structlog
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from orchestrator_adapter.app.config import OrchestratorSettings

_INIT_TIMEOUT: float = 30.0

# Story 11.3.6 — env-var allowlist forwarded to the THREE spawned MCP
# subprocesses (task-registry, session-registry, clawhip-bridge). Each
# server's ``__main__.py`` exits 2 if its REQUIRED vars are absent; with an
# env-less ``StdioServerParameters`` the MCP SDK forwards only
# ``get_default_environment()`` (a POSIX safe-list), so those required vars
# are stripped → subprocess exits → ``/tmp/ready`` is never touched →
# orchestrator-adapter is reported ``unhealthy`` on a fresh ROOT-compose boot.
#
# ⚠️ SECURITY (a0ca050 P0): this MUST stay an explicit allowlist. NEVER use
# ``env=os.environ.copy()`` / ``dict(os.environ)`` — that leaked ANTHROPIC_API_KEY
# / GITHUB_TOKEN / OPERATOR_HMAC_KEY into the MCP subprocesses (reverted twice).
# Mirrors the canon ``mcp-servers/task-registry/.../adapters/clawhip_client.py``
# ``_ENV_ALLOWLIST``, EXPANDED to the per-server required vars because this
# spawner connects to all three servers (the canon spawns only clawhip-bridge).
# Kept byte-identical to the worker-wrapper sibling — enforced by
# ``tests/contract/test_clawhip_client_env_allowlist_mirror.py``.
_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Process basics
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        # Python interpreter resolution
        "PYTHONPATH",
        "PYTHONUNBUFFERED",
        # Temp directories (Python ``tempfile`` checks these before /tmp)
        "TMPDIR",
        "TMP",
        "TEMP",
        # TLS / CA bundles (custom-CA deployments)
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        # task-registry REQUIRED (mcp-servers/task-registry/.../__main__.py)
        "TASK_REGISTRY_DB_PATH",
        "TASK_REGISTRY_ACTOR_KIND",
        "TASK_REGISTRY_ACTOR_ID",
        # session-registry REQUIRED (mcp-servers/session-registry/.../__main__.py)
        "SESSION_REGISTRY_DB_PATH",
        "SESSION_REGISTRY_ACTOR_KIND",
        "SESSION_REGISTRY_ACTOR_ID",
        # clawhip-bridge REQUIRED (mcp-servers/clawhip-bridge/.../__main__.py)
        "CLAWHIP_BRIDGE_ACTOR_KIND",
        "CLAWHIP_BRIDGE_ACTOR_ID",
        "CLAWHIP_BRIDGE_LOG_DIR",
        # git-mcp REQUIRED (mcp-servers/git/.../__main__.py) — Story 15.5
        "GIT_MCP_ACTOR_KIND",
        "GIT_MCP_ACTOR_ID",
        "GIT_MCP_WORKTREE_ROOT",
        # github-mcp REQUIRED (mcp-servers/github/.../__main__.py exits 2 without
        # these) — Story 16.5 / G-SEC-2. GITHUB_MCP_SCOPED_TOKEN is a NARROWLY-
        # SCOPED GitHub credential (fine-grained PAT / App installation token
        # scoped to the target repo), NOT the broad operator GITHUB_TOKEN — which
        # stays BANNED (below). This closes the MCP-SUBPROCESS half of G-SEC-2: an
        # MCP subprocess never sees the broad operator PAT, only a repo-scoped
        # token (leak blast-radius = one repo). NOTE: the claude-agent spawn path
        # (claude_code_runner _CHILD_ENV_ALLOWLIST) still forwards the broad PAT
        # for `git push` — that half remains open, tracked by its in-code TODO +
        # deferred-work. It IS a credential but a deliberately narrow one
        # (ADR-0010 §6 "scoped credentials use new, narrowly-named vars"); the
        # broad-secret denylist below remains excluded.
        "GITHUB_MCP_ACTOR_KIND",
        "GITHUB_MCP_ACTOR_ID",
        "GITHUB_MCP_SCOPED_TOKEN",
        # Shared event-log + SQLite paths (spine convention)
        "REGISTRY_EVENTS_DIR",
        "REGISTRY_DB_PATH",
        # Feature-flag mirror (task/session-registry read this for audit emission)
        "OMB_MCP_AUDIT_EMISSION_ENABLED",
        # NO ANTHROPIC_API_KEY, NO GITHUB_TOKEN, NO OPERATOR_HMAC_KEY, NO AWS/OPENAI.
    }
)


def _default_env_allowlist() -> dict[str, str]:
    """Return a fresh dict of parent-env vars matching ``_ENV_ALLOWLIST``."""
    return {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}


@dataclass
class MCPClientGroup:
    """Manages three MCP client connections via stdio subprocesses."""

    settings: OrchestratorSettings
    # Story 11.3.6: explicit allowlist (NEVER os.environ.copy) — forwarded to
    # every spawned MCP subprocess. Override at construction for tests.
    env: dict[str, str] = field(default_factory=_default_env_allowlist)
    _stack: AsyncExitStack | None = None

    task_registry: ClientSession | None = None
    session_registry: ClientSession | None = None
    clawhip_bridge: ClientSession | None = None

    async def __aenter__(self) -> MCPClientGroup:
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        try:
            self.task_registry = await self._connect(
                "task-registry",
                self.settings.task_registry_command,
                self.settings.task_registry_args,
            )
            self.session_registry = await self._connect(
                "session-registry",
                self.settings.session_registry_command,
                self.settings.session_registry_args,
            )
            self.clawhip_bridge = await self._connect(
                "clawhip-bridge",
                self.settings.clawhip_bridge_command,
                self.settings.clawhip_bridge_args,
            )
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(exc_type, exc_val, exc_tb)
            self._stack = None
        self.task_registry = None
        self.session_registry = None
        self.clawhip_bridge = None

    async def _connect(
        self,
        name: str,
        command: str,
        args: list[str],
    ) -> ClientSession:
        log = structlog.get_logger(__name__)
        # Story 11.3.6: forward the allowlisted env so each MCP server gets its
        # REQUIRED vars. The SDK merges this over get_default_environment().
        # `dict(self.env)` is a defensive per-call copy so a mutation in one
        # spawned server's startup path cannot affect a sibling's env (the 3
        # _connect calls share the same `self.env` reference otherwise).
        params = StdioServerParameters(command=command, args=args, env=dict(self.env))
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await asyncio.wait_for(session.initialize(), timeout=_INIT_TIMEOUT)
        log.info("mcp_client_connected", server=name)
        return session


async def _check_one(name: str, session: ClientSession | None) -> tuple[str, bool]:
    """Check a single MCP server, returning (name, ok)."""
    log = structlog.get_logger(__name__)
    if session is None:
        return (name, False)
    try:
        await session.list_tools()
        return (name, True)
    except Exception:
        log.exception("connectivity_check_failed", server=name)
        return (name, False)


async def verify_connectivity(clients: MCPClientGroup) -> dict[str, bool]:
    """Call ``list_tools()`` on each server to verify liveness."""
    log = structlog.get_logger(__name__)
    checks = [
        _check_one("task-registry", clients.task_registry),
        _check_one("session-registry", clients.session_registry),
        _check_one("clawhip-bridge", clients.clawhip_bridge),
    ]
    pairs = await asyncio.gather(*checks)
    results = dict(pairs)
    log.info("connectivity_check", results=results)
    return results
