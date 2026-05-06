"""Worker settings — MCP server commands configurable via environment (AC-4)."""

from __future__ import annotations

from typing import ClassVar

from events.ids import new_session_id, new_worker_id
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    """Configuration for worker-wrapper MCP client connections.

    Each MCP server is spawned as a subprocess via ``command + args``.
    Defaults use the workspace-relative ``python -m <module>`` pattern.
    Override via environment variables prefixed with ``WORKER_``
    (e.g. ``WORKER_TASK_REGISTRY_COMMAND``, ``WORKER_TASK_REGISTRY_ARGS``).
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="WORKER_",
    )

    task_registry_command: str = "python"
    task_registry_args: list[str] = ["-m", "task_registry_mcp"]

    session_registry_command: str = "python"
    session_registry_args: list[str] = ["-m", "session_registry_mcp"]

    clawhip_bridge_command: str = "python"
    clawhip_bridge_args: list[str] = ["-m", "clawhip_bridge_mcp"]

    # TODO(Story 5.8/5.9): consumed when task/session registry MCP servers
    # need a shared SQLite path.
    registry_db_path: str = ""

    ready_file_path: str = ""

    session_id: str = ""
    worker_id: str = ""
    task_id: str = ""
    heartbeat_interval_s: float = Field(default=30.0, gt=0)

    _resolved_session_id: str | None = None
    _resolved_worker_id: str | None = None

    def resolve_session_id(self) -> str:
        """Return ``session_id`` or generate a new UUIDv7 if empty (cached)."""
        if self._resolved_session_id is None:
            self._resolved_session_id = self.session_id or new_session_id()
        return self._resolved_session_id

    def resolve_worker_id(self) -> str:
        """Return ``worker_id`` or generate a new UUIDv7 if empty (cached)."""
        if self._resolved_worker_id is None:
            self._resolved_worker_id = self.worker_id or new_worker_id()
        return self._resolved_worker_id

    def resolve_task_id(self) -> str | None:
        """Return ``task_id`` if set and non-empty, else ``None``.

        Invalid values (e.g. ``WORKER_TASK_ID=garbage``) that don't match
        the ``_TASK_ID_PATTERN`` regex will cause a ``ValidationError``
        when the payload model is constructed — this is intentional: a
        misconfigured task ID should fail loudly *before* the session
        lifecycle starts.
        """
        return self.task_id or None
