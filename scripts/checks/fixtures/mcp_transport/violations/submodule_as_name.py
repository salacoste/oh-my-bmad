# Fixture: `from mcp.server import sse / streamable_http` + re-export name — VIOLATIONS (MCP001).
#
# Exercises the visit_ImportFrom paths for:
#   * forbidden submodule pulled as a name from the parent `mcp.server` package
#   * forbidden transport NAME (`sse_app`) imported from an arbitrary shim module
# All UNsuppressed.
from mcp.server import sse, streamable_http
from my.compat.shim import sse_app


def build() -> None:
    sse.SseServerTransport("/messages")
    streamable_http.streamable_http_app()
    sse_app()
