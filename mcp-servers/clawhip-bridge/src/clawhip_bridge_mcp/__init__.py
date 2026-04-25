"""clawhip-bridge-mcp — MCP server: append-only event-emission surface (Story 2.8).

Exports ``build_server`` (factory) and ``main`` (entrypoint shim).
SOLE mutation path to the event-log spine per FR26.
"""

from clawhip_bridge_mcp.__main__ import main
from clawhip_bridge_mcp.server import build_server

__version__ = "0.2.0"

__all__ = ["build_server", "main"]
