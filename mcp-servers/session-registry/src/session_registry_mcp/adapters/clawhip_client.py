"""clawhip-bridge MCP client adapter for session-registry (Story 11.2.2).

Spawns a stdio subprocess running the ``clawhip-bridge`` MCP server and
exposes an async ``emit_event(type, payload, **kwargs)`` method used by
the ``emit_capability_denied_on_deny`` decorator to route ``capability.denied``
audit envelopes through the FR26 single-writer surface.

Pattern mirrors ``services/orchestrator-adapter/src/orchestrator_adapter/
adapters/mcp_clients.py:MCPClientGroup`` and Story 5.1's worker-wrapper
equivalent. Lifespan-managed by ``app/main.py`` (startup-spawn + fail-loud,
PD-1 fail-soft mid-request — OQ-4 resolution).

NOTE: Body is byte-identical to the task-registry sibling adapter at
``mcp-servers/task-registry/src/task_registry_mcp/adapters/clawhip_client.py``
(modulo the docstring server name). mcp-servers cannot share code per
Story 5.8's import-graph constraint; this duplication matches the
existing precedent for ``validate_caller_trace_id`` across the three
MCP servers.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

from events.ids import new_uuid7  # noqa: IMP001 — packages/
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

log = logging.getLogger(__name__)

_INIT_TIMEOUT: float = 30.0


@dataclass
class ClawhipBridgeClient:
    """Manage a single stdio MCP connection to clawhip-bridge.

    Usage::

        async with ClawhipBridgeClient(
            command="python", args=["-m", "clawhip_bridge_mcp"]
        ) as client:
            await client.emit_event("capability.denied", {...})

    The connection is established in ``__aenter__`` (fail-loud on startup
    per OQ-4). After ``__aenter__``, ``emit_event`` may be invoked
    concurrently; the underlying ``ClientSession.call_tool`` serializes
    over the stdio pipe.
    """

    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    _stack: AsyncExitStack | None = None
    _session: ClientSession | None = None

    async def __aenter__(self) -> ClawhipBridgeClient:
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
                "clawhip_bridge_client_connected",
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

    async def emit_event(
        self,
        event_type: str,
        payload: dict[str, object],
        *,
        caller_trace_id: str | None = None,
        parent_event_id: str | None = None,
    ) -> None:
        """Invoke clawhip-bridge's ``emit_event`` tool.

        Routes through the MCP-RPC surface so the FR26 single-writer
        constraint is preserved (session-registry never writes to the event
        log itself; clawhip-bridge is the sole writer).

        ``caller_trace_id`` is auto-minted via ``new_uuid7()`` when None
        — the audit emission is system-emitted and synthesizes its own
        correlation if the calling context does not provide one.

        Raises whatever ``ClientSession.call_tool`` raises (RuntimeError,
        OSError on broken pipe, ValidationError on schema mismatch). The
        emit_capability_denied_on_deny decorator's PD-1 fail-soft block
        catches these so the original CapabilityDenied is still re-raised.
        """
        if self._session is None:
            raise RuntimeError(
                "ClawhipBridgeClient.emit_event called before __aenter__ "
                "(session is None — lifespan not entered)"
            )
        if caller_trace_id is None:
            caller_trace_id = new_uuid7()
        arguments: dict[str, object] = {
            "type": event_type,
            "payload": payload,
            "caller_trace_id": caller_trace_id,
        }
        if parent_event_id is not None:
            arguments["parent_event_id"] = parent_event_id
        await self._session.call_tool("emit_event", arguments)


class EmitterHolder:
    """Mutable container exposing a ``CapabilityDeniedEmitter``-shaped callable.

    Story 11.2.2 wiring problem: the ``@mcp.tool()`` decorators run at
    server-construction time (``register_tools``), but the live
    ``ClawhipBridgeClient`` connection is established later inside the
    FastMCP lifespan. The decorator needs a stable emitter callable at
    registration time; the holder is set inside the lifespan's startup
    block.

    ``emit_event`` is intentionally the same shape as
    ``capabilities.emit.CapabilityDeniedEmitter`` (``async (str, dict) -> None``)
    so it can be passed directly to ``emit_capability_denied_on_deny``.

    If the holder is invoked before the lifespan populates ``client``,
    a ``RuntimeError`` is raised — caught by the decorator's PD-1
    fail-soft block and logged at ERROR. This preserves the
    original ``CapabilityDenied`` re-raise (AC6) even if the lifespan
    failed to wire the client (test fixtures, misconfigured factory).
    """

    def __init__(self) -> None:
        self.client: ClawhipBridgeClient | None = None

    async def emit_event(self, event_type: str, payload: dict[str, object]) -> None:
        """Forward to ``self.client.emit_event``; raise if not yet wired."""
        if self.client is None:
            raise RuntimeError(
                "EmitterHolder.emit_event invoked before lifespan wired the "
                "ClawhipBridgeClient — capability.denied audit dropped (PD-1 fail-soft)"
            )
        await self.client.emit_event(event_type, payload)


__all__ = ["ClawhipBridgeClient", "EmitterHolder"]
