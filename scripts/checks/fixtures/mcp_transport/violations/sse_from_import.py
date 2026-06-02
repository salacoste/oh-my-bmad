# Fixture: `from mcp.server.sse import …` — VIOLATION (MCP001).
#
# The canonical non-stdio mount: importing SseServerTransport from the SSE
# submodule. UNsuppressed → must surface MCP001.
from mcp.server.sse import SseServerTransport


def mount() -> object:
    return SseServerTransport("/messages")
