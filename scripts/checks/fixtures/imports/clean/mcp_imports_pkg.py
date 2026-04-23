# Fixture: mcp-server importing from a package — allowed.
# Owner classification: ("mcp-server", "task-registry") — see _meta.py
from events import __version__  # allowed: mcp-server → package

__all__ = ["__version__"]
