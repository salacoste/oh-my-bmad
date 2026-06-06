"""Artifact-mcp client integration for screenshot storage (FR81 / NFR-B3).

Story 21.3: spawns a stdio MCP connection to the ``artifact-mcp`` server for
``artifact.put`` calls from ``browser_take_screenshot``. Pattern mirrors
``clawhip_client.py:ClawhipBridgeClient`` (same stdio lifecycle, same
``__aenter__`` / ``__aexit__`` discipline).

The ``ArtifactClientHolder`` mirrors ``EmitterHolder`` — a mutable container
so the tool handler can call ``put(...)`` at request time even though the
live client is wired inside the FastMCP lifespan.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

log = logging.getLogger(__name__)

_INIT_TIMEOUT: float = 30.0

# Env-var allowlist forwarded to the artifact-mcp subprocess.
# Keep tight — adding a var here widens the secret-leakage blast radius.
_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PYTHONPATH",
        "PYTHONUNBUFFERED",
        "TMPDIR",
        "TMP",
        "TEMP",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        # artifact-mcp runtime paths
        "ARTIFACT_STORE_DIR",
    }
)


def _default_env_allowlist() -> dict[str, str]:
    """Return a fresh dict of parent-env vars matching ``_ENV_ALLOWLIST``."""
    return {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}


@dataclass
class ArtifactClient:
    """Manage a single stdio MCP connection to artifact-mcp.

    Usage::

        async with ArtifactClient(command="python", args=["-m", "artifact_mcp"]) as ac:
            result = await ac.put(content=b"...", name="screenshot.png", task_id="t-...")

    The connection is established in ``__aenter__`` (fail-loud on startup per OQ-4).
    """

    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=lambda: _default_env_allowlist())
    _stack: AsyncExitStack | None = None
    _session: ClientSession | None = None

    async def __aenter__(self) -> ArtifactClient:
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        try:
            params = StdioServerParameters(
                command=self.command,
                args=self.args,
                env=self.env,
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(session.initialize(), timeout=_INIT_TIMEOUT)
            self._session = session
            log.info(
                "artifact_client_connected",
                extra={"command": self.command, "args": self.args},
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
        self._session = None

    async def put(
        self,
        *,
        caller_trace_id: str,
        content: bytes,
        name: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, object]:
        """Call ``artifact.put`` over the MCP stdio connection.

        Args:
            caller_trace_id: FR58 correlation ID.
            content: Raw bytes to store (base64-encoded on the wire).
            name: Optional logical name for the artifact.
            task_id: Optional originating task ID.

        Returns:
            The ``artifact.put`` result dict (includes ``hash``, ``size``, etc.).
        """
        if self._session is None:
            raise RuntimeError(
                "ArtifactClient.put called before __aenter__ "
                "(session is None — lifespan not entered)"
            )
        b64 = base64.b64encode(content).decode("ascii")
        result = await self._session.call_tool(
            "artifact.put",
            {
                "caller_trace_id": caller_trace_id,
                "content": b64,
                "name": name,
                "task_id": task_id or "",
            },
        )
        # Extract the result from CallToolResult text content.
        text = "; ".join(c.text for c in result.content if hasattr(c, "text"))
        return {"raw_result": text, "is_error": result.isError}


class ArtifactClientHolder:
    """Mutable container holding the live ``ArtifactClient``.

    Mirrors ``EmitterHolder`` — the tool handler can call ``put(...)`` at
    request time even though the live client is wired inside the FastMCP
    lifespan.
    """

    def __init__(self) -> None:
        self.client: ArtifactClient | None = None

    async def put(
        self,
        *,
        caller_trace_id: str,
        content: bytes,
        name: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, object]:
        """Delegate to the live ``ArtifactClient``."""
        if self.client is None:
            raise RuntimeError(
                "ArtifactClientHolder.put invoked before lifespan wired the ArtifactClient"
            )
        return await self.client.put(
            caller_trace_id=caller_trace_id,
            content=content,
            name=name,
            task_id=task_id,
        )


__all__ = ["ArtifactClient", "ArtifactClientHolder"]
