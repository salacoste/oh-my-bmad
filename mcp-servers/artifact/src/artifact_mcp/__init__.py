"""artifact-mcp — MCP server exposing a persisted content-addressed artifact store.

Epic 19 / ADR-0011. Stories 19.1 / 19.2 ship the scaffold: a ``build_server``
factory that wires the clawhip-bridge audit-emission lifespan (mirroring
task-registry / git-mcp / memory-mcp) and an :class:`~artifact_mcp.store.ArtifactStore`
backed by a DEDICATED content-addressed local filesystem subtree (stdlib
``hashlib`` for SHA-256 object naming + ``sqlite3`` raw SQL for the logical-name
index, write-temp-then-atomic-rename, re-hash-on-read tamper detection, and
operator-configurable TTL + total-size-cap retention) rooted at
``ARTIFACT_MCP_STORE_PATH``. The store ships now (19.2) so the content-addressing
/ dedup / tamper-detect / isolation / retention / file-mode reference contracts in
``test_tools_atdd.py`` are green; the artifact tools (``artifact.get`` /
``artifact.list`` / ``artifact.put`` / ``artifact.delete``) land in Stories
19.3 / 19.4 (``TIER_MAP`` is empty for now).
"""

from artifact_mcp.server import build_server

__version__ = "0.1.0"

__all__ = ["build_server"]
