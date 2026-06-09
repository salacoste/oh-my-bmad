# Fixture: streamable-http imports ARE allowed in __main__.py — CLEAN.
#
# In designated files (server entry points, auth middleware, mcp_clients.py)
# streamable-http imports are permitted by ADR-0022. To exercise the
# suppression path (self-test requires each clean file to contain >=1 forbidden
# node), one suppressed SSE import is included alongside the allowed
# streamable-http imports.
from mcp.server.streamable_http import streamable_http_app
from mcp.server.sse import SseServerTransport  # noqa: MCP001 — fixture: SSE still forbidden, suppressed
