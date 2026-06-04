"""memory-mcp — MCP server exposing a persistent cross-task knowledge store.

Epic 18 / ADR-0012. Stories 18.1 / 18.2 ship the scaffold: a ``build_server``
factory that wires the clawhip-bridge audit-emission lifespan (mirroring
task-registry / git-mcp) and a :class:`~memory_mcp.store.MemoryStore` backed by a
DEDICATED SQLite database (stdlib ``sqlite3`` + raw SQL, FTS5 full-text index) at
the path given by ``MEMORY_MCP_STORE_PATH``. The store ships now (18.2) so the
search/isolation/file-mode reference contracts in ``test_tools_atdd.py`` are
green; the memory tools (``memory.read`` / ``memory.search`` / ``memory.write``)
land in Stories 18.3 / 18.4 (``TIER_MAP`` is empty for now).
"""

from memory_mcp.server import build_server

__version__ = "0.1.0"

__all__ = ["build_server"]
