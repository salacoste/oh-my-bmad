"""Workspace root package for oh-my-bmad.

This module exists so the workspace root is itself a valid uv package; it has
no runtime behavior. Real platform code lives in `services/`, `packages/`, and
`mcp-servers/` workspace members.
"""

__version__ = "0.1.0"
