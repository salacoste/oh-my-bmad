# Fixture: bare `import mcp.server.sse` + streamable_http forms — VIOLATIONS (MCP001).
#
# Exercises the visit_Import path (forbidden module-path) for both the SSE and
# streamable-HTTP transports, including an aliased import. All UNsuppressed.
import mcp.server.sse
import mcp.server.streamable_http as transport


def serve() -> None:
    mcp.server.sse.SseServerTransport("/messages")
    transport.streamable_http_app()
