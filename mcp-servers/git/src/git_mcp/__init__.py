"""git-mcp — MCP server exposing bounded git operations over a sandboxed worktree.

Epic 15 / FR72. Story 15.2 ships the scaffold: a ``build_server`` factory that
wires the clawhip-bridge audit-emission lifespan (mirroring task-registry) and a
``GitExecutor`` whose worktree-containment logic is import-clean and tested. NO
git subprocess is spawned yet — the actual git invocation lands in Stories
15.3 / 15.4 alongside the first git tools (``TIER_MAP`` is empty for now).
"""

from git_mcp.server import build_server

__version__ = "0.1.0"

__all__ = ["build_server"]
