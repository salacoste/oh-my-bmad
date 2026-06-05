"""Unit tests for bounded liveness probe in ``_check_one``.

The ``_check_one`` function wraps ``session.list_tools()`` in
``asyncio.wait_for`` so a hung MCP server cannot block the connectivity
check indefinitely. These tests exercise both the timeout and success
paths using a mock ``ClientSession``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from worker_wrapper.adapters.mcp_clients import _PROBE_TIMEOUT, _check_one


class _FakeSession:
    """Lightweight stand-in for ``mcp.ClientSession`` with controllable ``list_tools``."""

    def __init__(self, *, list_tools_fn: Any = None) -> None:
        self._list_tools: Any = (
            list_tools_fn if list_tools_fn is not None else AsyncMock(return_value=None)
        )

    async def list_tools(self) -> None:
        await self._list_tools()


@pytest.mark.asyncio
async def test_responsive_server_passes_probe() -> None:
    """A server that answers ``list_tools()`` quickly is reported healthy."""
    session = _FakeSession()
    name, ok = await _check_one("test-server", session)  # type: ignore[arg-type]
    assert name == "test-server"
    assert ok is True


@pytest.mark.asyncio
async def test_none_session_is_unhealthy() -> None:
    """A ``None`` session (server not spawned) is reported unhealthy."""
    name, ok = await _check_one("absent-server", None)
    assert name == "absent-server"
    assert ok is False


@pytest.mark.asyncio
async def test_hung_server_detected_within_timeout() -> None:
    """A server whose ``list_tools()`` never completes is detected as unhealthy."""

    async def _hang() -> None:
        await asyncio.sleep(999)  # will be cancelled by wait_for

    session = _FakeSession(list_tools_fn=_hang)
    name, ok = await _check_one("hung-server", session)  # type: ignore[arg-type]
    assert name == "hung-server"
    assert ok is False


@pytest.mark.asyncio
async def test_slow_server_within_timeout_is_healthy() -> None:
    """A server that responds within the probe timeout is still healthy."""

    async def _slow_but_ok() -> None:
        await asyncio.sleep(_PROBE_TIMEOUT * 0.5)

    session = _FakeSession(list_tools_fn=_slow_but_ok)
    name, ok = await _check_one("slow-server", session)  # type: ignore[arg-type]
    assert name == "slow-server"
    assert ok is True


@pytest.mark.asyncio
async def test_exception_in_list_tools_is_unhealthy() -> None:
    """A server that raises during ``list_tools()`` is reported unhealthy."""

    async def _boom() -> None:
        raise RuntimeError("MCP server internal error")

    session = _FakeSession(list_tools_fn=_boom)
    name, ok = await _check_one("crashing-server", session)  # type: ignore[arg-type]
    assert name == "crashing-server"
    assert ok is False
