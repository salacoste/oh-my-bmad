# Fixture: forbidden non-stdio MCP transports used WITH `# noqa: MCP001 <reason>` — CLEAN.
#
# Every forbidden import below is explicitly suppressed with a non-empty reason,
# so the scanner must report ZERO MCP001 findings. This exercises the
# suppression path (mirrors check_no_subprocess.py clean fixtures).
from __future__ import annotations

import mcp.server.sse  # noqa: MCP001 — fixture: suppressed module import
import mcp.server.streamable_http as transport  # noqa: MCP001 — fixture: suppressed aliased import
from mcp.server import sse  # noqa: MCP001 — fixture: suppressed submodule-as-name
from mcp.server.sse import SseServerTransport  # noqa: MCP001 — fixture: suppressed from-import
from mcp.server.streamable_http import streamable_http_app  # noqa: MCP001 — fixture: suppressed app
from some.compat.shim import sse_app  # noqa: MCP001 — fixture: suppressed re-export name

__all__ = ["sse", "transport", "SseServerTransport", "streamable_http_app", "sse_app"]
