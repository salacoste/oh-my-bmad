"""Orchestrator-adapter settings — MCP commands and OMC subprocess config (Story 5.10)."""

from __future__ import annotations

from typing import ClassVar

from events.ids import new_worker_id
from pydantic import Field, PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict


class OrchestratorSettings(BaseSettings):
    """Configuration for the orchestrator-adapter service.

    MCP servers are spawned as subprocesses via ``command + args``.
    OMC path points to the vendored ``upstream/omc/`` directory.
    Override via environment variables prefixed with ``ORCHESTRATOR_``.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="ORCHESTRATOR_",
    )

    task_registry_command: str = "python"
    task_registry_args: list[str] = ["-m", "task_registry_mcp"]

    session_registry_command: str = "python"
    session_registry_args: list[str] = ["-m", "session_registry_mcp"]

    clawhip_bridge_command: str = "python"
    clawhip_bridge_args: list[str] = ["-m", "clawhip_bridge_mcp"]

    registry_db_path: str = ""
    ready_file_path: str = "/tmp/ready"

    actor_id: str = ""
    omc_path: str = "upstream/omc"
    omc_timeout_s: float = Field(default=120.0, gt=0)
    poll_interval_s: float = Field(default=5.0, gt=0)

    _resolved_actor_id: str | None = PrivateAttr(default=None)

    def resolve_actor_id(self) -> str:
        """Return ``actor_id`` or generate a new UUIDv7 if empty (cached)."""
        if self._resolved_actor_id is None:
            self._resolved_actor_id = self.actor_id or new_worker_id()
        return self._resolved_actor_id
