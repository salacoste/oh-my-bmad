"""session-registry — MCP server exposing active sessions / worker metadata / heartbeats (read) + lifecycle tools (session.heartbeat, session.register, session.close).

Story 1.2 ships only `__version__`. Real logic arrives in: Story 5.9 (session-registry MCP server read + bounded-write).
"""

__version__ = "0.1.0"
