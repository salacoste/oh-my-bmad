"""github-mcp — MCP server exposing bounded GitHub REST operations.

Epic 16 / FR73. Story 16.2 ships the scaffold: a ``build_server`` factory that
wires the clawhip-bridge audit-emission lifespan (mirroring git-mcp / task-
registry) over a GitHub REST adapter. NO GitHub API call is issued yet — the
actual REST invocation lands in Stories 16.3 / 16.4 alongside the first github
tools (``TIER_MAP`` is empty for now). Authentication uses the Story-16.5
scoped token (``GITHUB_MCP_SCOPED_TOKEN``), NEVER the broad ``GITHUB_TOKEN``.
"""

from github_mcp.server import build_server

__version__ = "0.1.0"

__all__ = ["build_server"]
