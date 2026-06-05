"""browser-mcp — MCP server exposing browser automation via Playwright MCP subprocess.

Epic 20 / FR78. Story 20.1 ships the scaffold: a ``build_server`` factory that
wires the clawhip-bridge audit-emission lifespan (mirroring task-registry) and a
blank ``TIER_MAP`` whose browser tools land in Stories 21.1-21.5. NO Playwright
subprocess is spawned yet — the actual subprocess management lands in Story 20.2.
"""

from browser_mcp.server import build_server

__version__ = "0.1.0"

__all__ = ["build_server"]
