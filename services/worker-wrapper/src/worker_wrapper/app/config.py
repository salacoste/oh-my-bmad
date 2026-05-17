"""Worker settings — MCP server commands configurable via environment (AC-4)."""

from __future__ import annotations

from typing import ClassVar

import structlog
from events.envelope import is_valid_trace_id
from events.ids import new_session_id, new_uuid7, new_worker_id
from pydantic import Field, SecretStr, field_validator
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
    worktree_path: str = ""
    heartbeat_interval_s: float = Field(default=30.0, gt=0)

    # Claude Code subprocess settings (Story 5.4).
    claude_command: str = "claude"
    claude_max_turns: int = 0  # 0 = unlimited
    claude_timeout_s: float = 600.0
    claude_output_format: str = "stream-json"
    anthropic_api_key: str = ""  # WORKER_ANTHROPIC_API_KEY

    # Approval gate settings (Story 6.7).
    approval_poll_interval_s: float = Field(default=2.0, gt=0)
    approval_timeout_s: float = Field(default=3600.0, gt=0)
    event_log_dir: str = ""

    # GitHub API settings (Story 5.7).
    github_token: SecretStr = SecretStr("")
    github_api_base_url: str = "https://api.github.com"
    github_timeout_s: float = Field(default=10.0, gt=0)

    # Story 9.6 / FR59 / NFR-O7 — trace_id propagation.
    trace_id: str | None = Field(
        default=None,
        description=(
            "Trace_id supplied by the spawning service for this worker invocation. "
            "Set via WORKER_TRACE_ID env var. Story 9.6 / FR59 / NFR-O7. "
            "Must match Story 9.1 contract (UUIDv7 or 'tg:<update_id>'); "
            "if absent or invalid, the worker mints a fresh UUIDv7 with a WARNING "
            "log. Threaded through Claude Code subprocess as OMB_TRACE_ID env var "
            "AND as caller_trace_id on every clawhip-bridge MCP tool call. "
            "Spec proposed OMB_WORKER_TRACE_ID but existing WorkerSettings prefix "
            "is WORKER_, so WORKER_TRACE_ID is the canonical env var name."
        ),
    )

    @field_validator("trace_id", mode="before")
    @classmethod
    def _validate_trace_id_shape(cls, value: object) -> str | None:
        """Per Story 9.6 AC2: present-but-invalid → log WARNING + return None
        (consumer mints fresh); absent → return None silently.

        Following Story 9.4 pass-2 lesson S2: production-safe paths use raise/log,
        not assert (asserts are stripped under ``python -O``). Following Story 9.1
        contract: ``is_valid_trace_id`` is the SHAPE oracle — use it instead of
        bare ``isinstance`` checks (Story 9.2 pass-2 N13 lesson).
        """
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            return None
        if not is_valid_trace_id(value):
            log = structlog.get_logger(__name__)
            # Preview-safe: cap at 80 chars per Story 9.3 pass-2 S1 lesson —
            # never echo a full untrusted token back to logs.
            log.warning(
                "worker_trace_id_invalid_will_mint_fresh",
                value_preview=value[:80],
            )
            return None
        return value

    _resolved_session_id: str | None = None
    _resolved_worker_id: str | None = None
    _resolved_trace_id: str | None = None

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

    def resolve_trace_id(self) -> str:
        """Return validated ``trace_id`` or mint+cache a fresh UUIDv7 (Story 9.6).

        AC5: minted ONCE per ``WorkerSettings`` instance — every emission within
        a single worker invocation shares the same trace_id. The cache makes the
        per-invocation singleton explicit and avoids drift between e.g. the
        Claude Code subprocess argv/env and the clawhip-bridge MCP calls.
        """
        if self._resolved_trace_id is None:
            self._resolved_trace_id = self.trace_id or new_uuid7()
        return self._resolved_trace_id

    def resolve_task_id(self) -> str | None:
        """Return ``task_id`` if set and non-empty, else ``None``.

        Invalid values (e.g. ``WORKER_TASK_ID=garbage``) that don't match
        the ``_TASK_ID_PATTERN`` regex will cause a ``ValidationError``
        when the payload model is constructed — this is intentional: a
        misconfigured task ID should fail loudly *before* the session
        lifecycle starts.
        """
        return self.task_id or None
