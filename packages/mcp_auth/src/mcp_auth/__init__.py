"""Bearer token auth middleware for MCP Streamable HTTP transport (Phase 10)."""

from __future__ import annotations

from mcp_auth.middleware import BearerTokenMiddleware
from mcp_auth.settings import McpAuthSettings

__all__ = ["BearerTokenMiddleware", "McpAuthSettings"]
