"""task-registry-mcp — MCP server: task list/detail/approval-queue/blockers (read-only resources) + bounded-write tools (task.add_note, task.attach_artifact, task.emit_event)."""

from task_registry_mcp.app.main import build_server

__version__ = "0.2.0"

__all__ = ["build_server"]
