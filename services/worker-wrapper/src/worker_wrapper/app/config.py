"""Worker settings — MCP server commands configurable via environment (AC-4)."""

from __future__ import annotations

from typing import Any, ClassVar

import structlog
from events.envelope import is_valid_trace_id
from events.ids import new_session_id, new_uuid7, new_worker_id
from pydantic import AliasChoices, Field, PrivateAttr, SecretStr, field_validator
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
        populate_by_name=True,
    )

    # ------------------------------------------------------------------
    # Public fields (Story 9.6 review pass-1 L3 — grouped together).
    # ------------------------------------------------------------------

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

    # Story 9.6 review pass-1 H2 — CLI flag gating.
    # Default OFF until Claude Code upstream consumes ``--trace-id``. The
    # ``OMB_TRACE_ID`` env var (set by ``_spawn``) is the non-breaking
    # surface that ships today; the CLI flag is opt-in until verified.
    emit_trace_id_flag: bool = False

    # Story 9.6 / FR59 / NFR-O7 — trace_id propagation.
    #
    # Review pass-1 M7: a spawning service may intuitively export either of
    # ``WORKER_TRACE_ID`` (canonical), ``OMB_WORKER_TRACE_ID`` (spec name), or
    # ``OMB_TRACE_ID`` (subprocess-facing name). ``AliasChoices`` accepts all
    # three so the worker never silently mints a fresh UUIDv7 when the
    # spawner sets the "wrong" name.
    trace_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "WORKER_TRACE_ID",
            "OMB_WORKER_TRACE_ID",
            "OMB_TRACE_ID",
        ),
        description=(
            "Trace_id supplied by the spawning service for this worker invocation. "
            "Set via WORKER_TRACE_ID (canonical), OMB_WORKER_TRACE_ID, or "
            "OMB_TRACE_ID env vars (all three are accepted via AliasChoices — "
            "review pass-1 M7). Story 9.6 / FR59 / NFR-O7. Must match Story 9.1 "
            "contract (UUIDv7 or 'tg:<update_id>'); if absent or invalid, the "
            "worker mints a fresh UUIDv7 with a WARNING log. Threaded through "
            "Claude Code subprocess as OMB_TRACE_ID env var AND as "
            "caller_trace_id on every clawhip-bridge MCP tool call."
        ),
    )

    # ------------------------------------------------------------------
    # Validators (Story 9.6 review pass-1 L3 — grouped after public fields).
    # ------------------------------------------------------------------

    @field_validator("trace_id", mode="before")
    @classmethod
    def _validate_trace_id_shape(cls, value: object) -> str | None:
        """Per Story 9.6 AC2: present-but-invalid → log WARNING + return None
        (consumer mints fresh); absent → return None silently.

        Following Story 9.4 pass-2 lesson S2: production-safe paths use raise/log,
        not assert (asserts are stripped under ``python -O``). Following Story 9.1
        contract: ``is_valid_trace_id`` is the SHAPE oracle — use it instead of
        bare ``isinstance`` checks (Story 9.2 pass-2 N13 lesson).

        Review pass-1 M2 / L4 / H8: empty string and non-string values log a
        WARNING (no longer silent), and the warning preview uses ``repr()`` so
        any CRLF / NULL / ANSI / RTL override / ZWJ in the env var is escaped
        instead of injected into structured logs.
        """
        # Absent — silent None per AC2.
        if value is None:
            return None
        log = structlog.get_logger(__name__)
        # Empty string is "present-but-invalid" (a spawner bug), not "absent".
        # Review pass-1 M2: log a WARNING instead of silently returning None.
        if value == "":
            log.warning(
                "worker_trace_id_invalid_will_mint_fresh",
                value_preview=repr(""),  # review pass-1 H8 — escaped preview
                reason="empty_string",
            )
            return None
        # Review pass-1 L4: non-string types also log a WARNING (merged branch
        # with the shape failure below — single canonical log event).
        if not isinstance(value, str) or not is_valid_trace_id(value):
            # Build a safe preview: ``repr`` on a string escapes control chars
            # (CRLF, NULL, ANSI, ZWJ U+200D, RTL override U+202E, etc.).
            preview_src = value if isinstance(value, str) else str(value)
            log.warning(
                "worker_trace_id_invalid_will_mint_fresh",
                # Review pass-1 H8 — repr() so log lines can't be smuggled.
                value_preview=repr(preview_src[:80]),
                reason="shape_mismatch",
            )
            return None
        return value

    # ------------------------------------------------------------------
    # Private attrs (Story 9.6 review pass-1 H5 — PrivateAttr declarations).
    # ------------------------------------------------------------------
    #
    # Pre-pass-1 these were plain ``str | None = None`` class attrs; mutating
    # them on an instance worked but was a Pydantic anti-pattern (class-level
    # data unless declared ``PrivateAttr``). Tests previously reached in via
    # ``settings._resolved_trace_id = None`` — that hack is no longer needed
    # because ``model_post_init`` (H4) eagerly populates these before any
    # concurrent reader sees them.

    _resolved_session_id: str | None = PrivateAttr(default=None)
    _resolved_worker_id: str | None = PrivateAttr(default=None)
    _resolved_trace_id: str | None = PrivateAttr(default=None)

    # ------------------------------------------------------------------
    # Lifecycle hooks (Story 9.6 review pass-1 H4).
    # ------------------------------------------------------------------

    def model_post_init(self, __context: Any) -> None:
        """Eagerly resolve trace_id at instance construction.

        Review pass-1 H4: the previous "check-then-act" pattern inside
        :meth:`resolve_trace_id` was racy — multiple coroutines
        (``heartbeat_loop``, ``start_session``, ``run_task``) could read
        ``_resolved_trace_id`` before any single coroutine wrote it, each
        minting a different ``new_uuid7()``. Resolving eagerly here means the
        field is populated before any concurrent reader exists; subsequent
        ``resolve_trace_id()`` calls are pure reads.

        ``session_id`` / ``worker_id`` retain their lazy pattern because they
        are not similarly racy (they're only resolved from the main coroutine
        in ``start_session``).
        """
        # Local var pattern (review pass-1 M1) — strictly-typed ``str`` is
        # assigned both to the private cache and returned by ``resolve_*``.
        resolved: str = self.trace_id or new_uuid7()
        self._resolved_trace_id = resolved

    # ------------------------------------------------------------------
    # Methods (Story 9.6 review pass-1 L3 — grouped after private attrs).
    # ------------------------------------------------------------------

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
        """Return the validated ``trace_id`` (Story 9.6).

        Review pass-1 H4 + M1: ``model_post_init`` eagerly resolves the value
        at construction so this method is a pure read. The narrowing assert
        (review pass-1 M1) keeps mypy --strict happy without coercing the
        type at call sites.
        """
        # ``model_post_init`` guarantees this is non-None for any
        # successfully-constructed instance — narrow the Optional for mypy.
        if self._resolved_trace_id is None:
            raise RuntimeError(
                "model_post_init must have populated _resolved_trace_id"
            )
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
