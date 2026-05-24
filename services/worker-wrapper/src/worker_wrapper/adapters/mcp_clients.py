"""MCP client adapter — manages three stdio client connections (AC-1, AC-3)."""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass

import structlog
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from worker_wrapper.app.config import WorkerSettings

_INIT_TIMEOUT: float = 30.0


@dataclass
class MCPClientGroup:
    """Manages three MCP client connections via stdio subprocesses.

    Usage::

        async with MCPClientGroup(settings) as clients:
            tools = await clients.task_registry.list_tools()
            await clients.clawhip_bridge.call_tool("emit_event", ...)
    """

    settings: WorkerSettings
    _stack: AsyncExitStack | None = None

    # Populated after __aenter__
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
        params = StdioServerParameters(command=command, args=args, env=os.environ.copy())
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
    """Call ``list_tools()`` on each server to verify liveness (AC-2)."""
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
