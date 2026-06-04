"""verification-mcp — MCP server running build/test recipes sandboxed to a worktree.

Epic 17 / FR74. Story 17.2 ships the scaffold: a ``build_server`` factory that
wires the clawhip-bridge audit-emission lifespan (mirroring git-mcp /
task-registry) and a ``VerificationExecutor`` whose worktree-containment logic is
import-clean and tested. NO verification subprocess is spawned yet — the actual
recipe invocation lands in Stories 17.3 / 17.4 alongside the first verification
tools (``TIER_MAP`` is empty for now).
"""

from verification_mcp.server import build_server

__version__ = "0.1.0"

__all__ = ["build_server"]
