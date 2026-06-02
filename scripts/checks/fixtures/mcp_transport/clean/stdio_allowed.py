# Fixture: legitimate stdio transport is ALLOWED (never flagged) — CLEAN.
#
# The stdio imports below must produce ZERO findings. To also exercise the
# suppression path (self-test requires each clean file to contain >=1 forbidden
# node), one suppressed forbidden import is included alongside the allowed ones.
from __future__ import annotations

import mcp.server  # allowed: parent package, not a transport submodule
from mcp import types  # allowed: top-level mcp import
from mcp.server import Server  # allowed: server class, not a transport submodule
from mcp.server.stdio import stdio_server  # allowed: stdio IS the sanctioned transport

from mcp.server.sse import SseServerTransport  # noqa: MCP001 — fixture: suppressed alongside stdio

__all__ = ["mcp", "types", "Server", "stdio_server", "SseServerTransport"]
