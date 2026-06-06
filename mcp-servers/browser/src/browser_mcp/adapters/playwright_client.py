"""Playwright MCP stdio client adapter (Story 21.1 / FR79).

Wraps an already-spawned Playwright MCP subprocess's stdin/stdout pipes
as an ``mcp.ClientSession`` so tool handlers can forward calls over
the MCP JSON-RPC protocol.

Pattern mirrors ``mcp.client.stdio.stdio_client`` but wraps an
**already-spawned** process instead of spawning a new one. The process
lifecycle is managed by ``PlaywrightSubprocessManager`` — this client
only opens/closes the MCP session over the existing pipes.

Transport uses anyio memory object streams bridged to the process's
stdio, identical to how ``stdio_client`` works internally.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any

import anyio
from anyio.streams.memory import (
    MemoryObjectReceiveStream,
    MemoryObjectSendStream,
)
from anyio.streams.text import TextReceiveStream
from mcp import ClientSession
from mcp.shared.message import SessionMessage
from mcp.types import CallToolResult, JSONRPCMessage

log = logging.getLogger(__name__)

_INIT_TIMEOUT: float = 30.0
_CALL_TIMEOUT: float = 60.0


class PlaywrightMCPClient:
    """MCP client session over an existing Playwright subprocess's stdio pipes.

    Usage::

        client = PlaywrightMCPClient(proc)
        async with client:
            result = await client.call_tool("browser_navigate", {"url": "..."})
    """

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception] | None = None
        self._write_stream_reader: MemoryObjectReceiveStream[SessionMessage] | None = None
        self._bg_tasks: list[asyncio.Task[None]] = []

    @property
    def session(self) -> ClientSession | None:
        """The underlying MCP ClientSession (None before ``__aenter__``)."""
        return self._session

    async def __aenter__(self) -> PlaywrightMCPClient:
        """Establish MCP session over the process's stdin/stdout."""
        if self._proc.stdin is None or self._proc.stdout is None:
            raise RuntimeError(
                "Playwright subprocess must have stdin and stdout pipes "
                "for MCP transport. proc.stdin or proc.stdout is None."
            )

        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        try:
            # Create anyio memory object streams — same pattern as mcp.client.stdio.stdio_client.
            read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
            read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]

            write_stream: MemoryObjectSendStream[SessionMessage]
            write_stream_reader: MemoryObjectReceiveStream[SessionMessage]

            read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
            write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

            self._read_stream_writer = read_stream_writer
            self._write_stream_reader = write_stream_reader

            # Background task: read JSON lines from proc.stdout → read_stream.
            self._bg_tasks.append(
                asyncio.create_task(
                    self._stdout_reader(read_stream_writer),
                    name="pw-mcp-stdout-reader",
                )
            )
            # Background task: read write_stream → write JSON lines to proc.stdin.
            self._bg_tasks.append(
                asyncio.create_task(
                    self._stdin_writer(write_stream_reader),
                    name="pw-mcp-stdin-writer",
                )
            )

            session = await self._stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await asyncio.wait_for(session.initialize(), timeout=_INIT_TIMEOUT)
            self._session = session
            log.info(
                "playwright_mcp_client_connected",
                extra={"pid": self._proc.pid},
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
        """Close the MCP session (does NOT kill the subprocess)."""
        # Cancel background reader/writer tasks.
        for task in self._bg_tasks:
            task.cancel()
        self._bg_tasks.clear()

        # Close memory streams to unblock pending reads/writes.
        if self._read_stream_writer is not None:
            await self._read_stream_writer.aclose()
            self._read_stream_writer = None
        if self._write_stream_reader is not None:
            await self._write_stream_reader.aclose()
            self._write_stream_reader = None

        if self._stack is not None:
            try:
                await self._stack.__aexit__(exc_type, exc_val, exc_tb)
            finally:
                self._stack = None
                self._session = None

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float = _CALL_TIMEOUT,
    ) -> CallToolResult:
        """Forward a tool call to the Playwright MCP subprocess.

        Args:
            name: MCP tool name (e.g. ``"browser_navigate"``).
            arguments: Tool arguments dict.
            timeout: Per-call timeout in seconds.

        Returns:
            The ``CallToolResult`` from the Playwright subprocess.

        Raises:
            RuntimeError: If the client session is not initialized.
            asyncio.TimeoutError: If the call exceeds *timeout*.
        """
        if self._session is None:
            raise RuntimeError(
                "PlaywrightMCPClient session not initialized. "
                "Use ``async with client:`` first."
            )
        result = await asyncio.wait_for(
            self._session.call_tool(name, arguments or {}),
            timeout=timeout,
        )
        return result

    @property
    def is_alive(self) -> bool:
        """Check if the underlying process is still running."""
        return self._proc.returncode is None

    # -- Background transport tasks (same pattern as mcp.client.stdio) -------

    async def _stdout_reader(
        self,
        writer: MemoryObjectSendStream[SessionMessage | Exception],
    ) -> None:
        """Read JSON lines from proc.stdout and send as SessionMessage."""
        assert self._proc.stdout is not None
        try:
            async with writer:
                buffer = ""
                async for chunk in TextReceiveStream(self._proc.stdout):
                    lines = (buffer + chunk).split("\n")
                    buffer = lines.pop()

                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            message = JSONRPCMessage.model_validate_json(line)
                        except Exception as exc:
                            log.exception("Failed to parse JSONRPC message from Playwright")
                            await writer.send(exc)
                            continue

                        session_message = SessionMessage(message)
                        await writer.send(session_message)
        except anyio.ClosedResourceError:
            await asyncio.sleep(0)  # checkpoint

    async def _stdin_writer(
        self,
        reader: MemoryObjectReceiveStream[SessionMessage],
    ) -> None:
        """Read SessionMessage from write_stream and send JSON to proc.stdin."""
        assert self._proc.stdin is not None
        try:
            async with reader:
                async for session_message in reader:
                    json_str = session_message.message.model_dump_json(
                        by_alias=True, exclude_none=True,
                    )
                    self._proc.stdin.write((json_str + "\n").encode())
                    await self._proc.stdin.drain()
        except anyio.ClosedResourceError:
            await asyncio.sleep(0)  # checkpoint
        except (ConnectionResetError, BrokenPipeError):
            log.warning("playwright_stdin_pipe_broken", exc_info=True)
