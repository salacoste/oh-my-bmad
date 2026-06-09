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
from mtls import create_httpx_verify_arg

from orchestrator_adapter.app.config import OrchestratorSettings

_INIT_TIMEOUT: float = 30.0
_PROBE_TIMEOUT: float = 10.0

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
        # token (leak blast-radius = one repo). The claude-agent spawn paths
        # (claude_code_runner + omc_runner _CHILD_ENV_ALLOWLIST) also EXCLUDE the
        # broad PAT — G-SEC-2 agent-spawn half CLOSED 2026-06-05, so G-SEC-2 is
        # now FULLY closed. It IS a credential but a deliberately narrow one
        # (ADR-0010 §6 "scoped credentials use new, narrowly-named vars"); the
        # broad-secret denylist below remains excluded.
        "GITHUB_MCP_ACTOR_KIND",
        "GITHUB_MCP_ACTOR_ID",
        "GITHUB_MCP_SCOPED_TOKEN",
        # verification-mcp REQUIRED (mcp-servers/verification/.../__main__.py exits
        # 2 without these) — Story 17.5. All NON-secret: a worktree-root path + the
        # actor identity. verification runs build/test recipes in the worktree
        # sandbox and needs NO external credential, so there is NO scoped-token
        # entry here (unlike github-mcp). Forwarded by BOTH spawner allowlists
        # (byte-identical mirror); only worker-wrapper actually spawns it
        # (conditional on a non-blank WORKER_VERIFICATION_COMMAND).
        "VERIFICATION_MCP_WORKTREE_ROOT",
        "VERIFICATION_MCP_ACTOR_KIND",
        "VERIFICATION_MCP_ACTOR_ID",
        # memory-mcp REQUIRED (mcp-servers/memory/.../__main__.py exits 2 without
        # these) — Story 18.5. All NON-secret: MEMORY_MCP_STORE_PATH is the path to
        # memory-mcp's OWN dedicated SQLite store (NEVER the registry DB — P3-I2
        # isolation) + the actor identity. No external credential, so NO scoped
        # token. Forwarded by BOTH spawner allowlists (byte-identical mirror); only
        # worker-wrapper actually spawns memory-mcp (conditional on a non-blank
        # WORKER_MEMORY_COMMAND).
        "MEMORY_MCP_STORE_PATH",
        "MEMORY_MCP_ACTOR_KIND",
        "MEMORY_MCP_ACTOR_ID",
        # artifact-mcp REQUIRED (mcp-servers/artifact/.../__main__.py exits 2 without
        # the first three) — Story 19.5. All NON-secret: ARTIFACT_MCP_STORE_PATH is
        # the artifact-mcp's OWN content-store root (NEVER the registry DB — P3-I2)
        # + the actor identity; the two RETENTION vars are optional operator policy
        # (size cap / TTL). No external credential, so NO scoped token. Forwarded by
        # BOTH spawner allowlists (byte-identical mirror); only worker-wrapper spawns
        # artifact-mcp (conditional on a non-blank WORKER_ARTIFACT_COMMAND).
        "ARTIFACT_MCP_STORE_PATH",
        "ARTIFACT_MCP_ACTOR_KIND",
        "ARTIFACT_MCP_ACTOR_ID",
        "ARTIFACT_MCP_RETENTION_MAX_BYTES",
        "ARTIFACT_MCP_RETENTION_TTL_SECONDS",
        # browser-mcp REQUIRED — Story 20.6. All NON-secret. Byte-identical
        # mirror of worker-wrapper's _ENV_ALLOWLIST.
        "BROWSER_MCP_ACTOR_KIND",
        "BROWSER_MCP_ACTOR_ID",
        "BROWSER_MCP_PLAYWRIGHT_IMAGE",
        "BROWSER_MCP_EXTRA_CAPS",
        "BROWSER_MCP_ALLOWED_HOSTS",
        "BROWSER_MCP_ALLOWED_ORIGINS",
        "BROWSER_MCP_MEMORY_LIMIT",
        "BROWSER_MCP_CPU_LIMIT",
        # Shared event-log + SQLite paths (spine convention)
        "REGISTRY_EVENTS_DIR",
        "REGISTRY_DB_PATH",
        # Feature-flag mirror (task/session-registry read this for audit emission)
        "OMB_MCP_AUDIT_EMISSION_ENABLED",
        # NO ANTHROPIC_API_KEY, NO GITHUB_TOKEN, NO OPERATOR_HMAC_KEY, NO AWS/OPENAI.
    }
)


# Story 43.1 (G-SEC-2 defense-in-depth): base env vars needed by every MCP child.
# Per-server vars are in _SERVER_REQUIRED_ENV below.
# ⚠️ This frozenset MUST be identical to the one in
# worker-wrapper/adapters/mcp_clients.py — enforced by
# tests/contract/test_clawhip_client_env_allowlist_mirror.py.
_BASE_ENV_VARS: frozenset[str] = frozenset(
    {
        # Process fundamentals
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        # Python runtime
        "PYTHONPATH",
        "PYTHONUNBUFFERED",
        "TMPDIR",
        "TMP",
        "TEMP",
        # TLS/CA bundles
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        # Shared infrastructure (all servers need these to connect)
        "REGISTRY_EVENTS_DIR",
        "REGISTRY_DB_PATH",
        # Audit emission flag
        "OMB_MCP_AUDIT_EMISSION_ENABLED",
    }
)

# Story 43.1 (G-SEC-2 defense-in-depth): per-server env vars.
# Each MCP child only receives _BASE_ENV_VARS + its own entry here.
_SERVER_REQUIRED_ENV: dict[str, frozenset[str]] = {
    "task-registry": frozenset(
        {
            "TASK_REGISTRY_DB_PATH",
            "TASK_REGISTRY_ACTOR_KIND",
            "TASK_REGISTRY_ACTOR_ID",
        }
    ),
    "session-registry": frozenset(
        {
            "SESSION_REGISTRY_DB_PATH",
            "SESSION_REGISTRY_ACTOR_KIND",
            "SESSION_REGISTRY_ACTOR_ID",
        }
    ),
    "clawhip-bridge": frozenset(
        {
            "CLAWHIP_BRIDGE_ACTOR_KIND",
            "CLAWHIP_BRIDGE_ACTOR_ID",
            "CLAWHIP_BRIDGE_LOG_DIR",
        }
    ),
}


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
            # Phase 10 / ADR-0022: each server connects via URL (streamable-http)
            # when a URL setting is provided, falling back to stdio otherwise.
            # The three always-present servers always connect — URL or command,
            # never both.
            self.task_registry = await self._connect(
                "task-registry",
                self.settings.task_registry_command,
                self.settings.task_registry_args,
                url=self.settings.task_registry_url or None,
            )
            self.session_registry = await self._connect(
                "session-registry",
                self.settings.session_registry_command,
                self.settings.session_registry_args,
                url=self.settings.session_registry_url or None,
            )
            self.clawhip_bridge = await self._connect(
                "clawhip-bridge",
                self.settings.clawhip_bridge_command,
                self.settings.clawhip_bridge_args,
                url=self.settings.clawhip_bridge_url or None,
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

    def _get_auth_token(self) -> str | None:
        """Read auth token for streamable-http MCP connections (Phase 10 / ADR-0022).

        Source priority:
        1. MCP_AUTH_TOKEN env var (pre-generated token)
        2. None (no auth — will fail if server requires auth)
        """
        return os.environ.get("MCP_AUTH_TOKEN", "").strip() or None

    async def _connect(
        self,
        name: str,
        command: str,
        args: list[str],
        url: str | None = None,
    ) -> ClientSession:
        log = structlog.get_logger(__name__)
        # Phase 10 / ADR-0022: URL and command are mutually exclusive.
        if url and command:
            raise ValueError(
                f"{name}: URL and command are mutually exclusive. "
                f"Got url={url!r} and command={command!r}"
            )
        # Phase 10 / ADR-0022: streamable-http transport when URL is set.
        if url:
            import httpx as _httpx
            from mcp.client.streamable_http import (
                streamable_http_client,  # noqa: I001, MCP001 — ADR-0022: streamable-http allowed in mcp_clients.py
            )

            token = self._get_auth_token()
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            # Phase 11 / ADR-0023: mTLS client TLS — pass a pre-configured
            # httpx.AsyncClient with the mTLS verify argument.
            http_client = _httpx.AsyncClient(
                headers=headers,
                verify=create_httpx_verify_arg(),
            )
            transport_context = streamable_http_client(url=url, http_client=http_client)
            read_write = await self._stack.enter_async_context(transport_context)
            session = await self._stack.enter_async_context(ClientSession(*read_write))
            await asyncio.wait_for(session.initialize(), timeout=_INIT_TIMEOUT)
            log.info("mcp_client_connected", server=name, transport="streamable-http", url=url)
            return session
        # Stdio transport — default path (Phase 9 baseline, unchanged).
        # Story 43.1: per-server env scoping for defense-in-depth.
        # Each MCP child only receives _BASE_ENV_VARS + its own server-specific vars.
        server_specific = _SERVER_REQUIRED_ENV.get(name, frozenset())
        allowed_vars = _BASE_ENV_VARS | server_specific
        filtered_env = {k: v for k, v in self.env.items() if k in allowed_vars}
        params = StdioServerParameters(command=command, args=args, env=filtered_env)
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
        await asyncio.wait_for(session.list_tools(), timeout=_PROBE_TIMEOUT)
        return (name, True)
    except TimeoutError:
        log.warning("connectivity_check_timeout", server=name, timeout=_PROBE_TIMEOUT)
        return (name, False)
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
