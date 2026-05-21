"""Worker settings — MCP server commands configurable via environment (AC-4)."""

from __future__ import annotations

import os
from typing import Any, ClassVar

import structlog
from events.envelope import is_valid_trace_id
from events.ids import new_session_id, new_uuid7, new_worker_id
from pydantic import AliasChoices, Field, PrivateAttr, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Story 9.6 review pass-2 PH5 — shared module-level constant referenced by the
# autouse fixture in :mod:`worker_wrapper.conftest`.  Listing all three accepted
# env-var names in one place keeps the fixture in sync with ``AliasChoices``.
_TRACE_ID_ALIASES: tuple[str, ...] = (
    "WORKER_TRACE_ID",
    "OMB_WORKER_TRACE_ID",
    "OMB_TRACE_ID",
)
# Review pass-2 PH7 / PM3 — the H2 flag env var (canonical name post-PH7) is
# also stripped by the autouse fixture so CI exports cannot leak in.
_EMIT_TRACE_ID_FLAG_ENV: str = "WORKER_EMIT_TRACE_ID_FLAG"


def _safe_trace_preview(tid: str) -> str:
    """Return an 8-char bounded preview suitable for structured log fields.

    Story 9.6 review pass-3 TM2: extracted helper shared across config /
    __main__ log call sites (PM11 ready-log + PM13 mint-log).
    """
    return tid[:8] + "..."


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

    # Story 12.1 pass-1 review PP4 — defensive ceiling on ``runner.run()`` to
    # prevent a deadlock when the budget supervisor has SIGKILLed the
    # subprocess but the runner's stdout-read loop fails to observe EOF
    # cleanly. Well above ``claude_timeout_s`` so it only trips on pathological
    # subprocess hangs (process killed but pipe-reader stuck in syscall).
    task_overall_timeout_s: float = Field(default=900.0, gt=0)

    # GitHub API settings (Story 5.7).
    github_token: SecretStr = SecretStr("")
    github_api_base_url: str = "https://api.github.com"
    github_timeout_s: float = Field(default=10.0, gt=0)

    # Story 9.6 review pass-1 H2 — CLI flag gating.
    # Default OFF until Claude Code upstream consumes ``--trace-id``. The
    # ``OMB_TRACE_ID`` env var (set by ``_spawn``) is the non-breaking
    # surface that ships today; the CLI flag is opt-in until verified.
    #
    # Review pass-2 PH7: canonical env var name is ``WORKER_EMIT_TRACE_ID_FLAG``
    # (no double-prefix stutter).  ``WORKER_WORKER_EMIT_TRACE_ID_FLAG`` is kept
    # as a backwards-compat alias only — operators / CI should migrate to the
    # canonical name.  ``populate_by_name=True`` lets ``WorkerSettings(emit_trace_id_flag=...)``
    # keep working via the field name itself.
    emit_trace_id_flag: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "WORKER_EMIT_TRACE_ID_FLAG",
            "WORKER_WORKER_EMIT_TRACE_ID_FLAG",
        ),
    )

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
            "trace_id",  # review pass-2 PM10 — natural ctor kwarg name
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

        # Review pass-2 PH1: when the canonical (first) AliasChoices entry
        # supplies an invalid value (``""`` or shape-fail), Pydantic does NOT
        # try the remaining aliases — alias-first-wins.  A spawner using
        # shell idiom ``${WORKER_TRACE_ID:-}`` would silently blackhole a
        # valid ``OMB_TRACE_ID`` fallback.  Walk the remaining aliases here
        # and accept the first valid one before falling through to the
        # warn-and-mint path.
        def _try_fallback_aliases() -> str | None:
            for fallback_env in ("OMB_WORKER_TRACE_ID", "OMB_TRACE_ID"):
                candidate = os.environ.get(fallback_env, "")
                if not candidate:
                    continue
                if is_valid_trace_id(candidate):
                    # Story 9.6 review pass-3 TL1 — info log when fallback
                    # fires so operators know which alias was accepted.
                    log.info(
                        "trace_id_alias_fallthrough_accepted",
                        alias_env=fallback_env,
                    )
                    return candidate
                # Story 9.6 review pass-3 TM3 — per-fallback invalid warning
                # so operators can diagnose which alias carried a bad value
                # (not just the final summary).
                log.warning(
                    "trace_id_alias_invalid",
                    alias_env=fallback_env,
                    value_preview=repr(candidate[:80]),
                )
            return None

        # Empty string is "present-but-invalid" (a spawner bug), not "absent".
        # Review pass-1 M2: log a WARNING instead of silently returning None.
        if value == "":
            fallback = _try_fallback_aliases()
            if fallback is not None:
                return fallback
            log.warning(
                "worker_trace_id_invalid_will_mint_fresh",
                value_preview=repr(""),  # review pass-1 H8 — escaped preview
                reason="empty_string",
            )
            return None
        # Review pass-1 L4: non-string types also log a WARNING (merged branch
        # with the shape failure below — single canonical log event).
        if not isinstance(value, str) or not is_valid_trace_id(value):
            # Review pass-2 PH1 — alias-fallthrough also covers shape-fail.
            fallback = _try_fallback_aliases()
            if fallback is not None:
                return fallback
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
        """Eagerly resolve trace_id / session_id / worker_id at construction.

        Review pass-1 H4: the previous "check-then-act" pattern inside
        :meth:`resolve_trace_id` was racy — multiple coroutines
        (``heartbeat_loop``, ``start_session``, ``run_task``) could read
        ``_resolved_trace_id`` before any single coroutine wrote it, each
        minting a different ``new_uuid7()``. Resolving eagerly here means the
        field is populated before any concurrent reader exists; subsequent
        ``resolve_trace_id()`` calls are pure reads.

        Review pass-2 PM5: ``session_id`` / ``worker_id`` are resolved eagerly
        too — symmetric with trace_id.  Although they're nominally
        main-coroutine-only, defence-in-depth removes a class of "lazy
        check-then-act" foot-guns that future async refactors could trip on.

        Review pass-2 PM6 + PM12: the type-narrowing is explicit
        (``if self.trace_id is not None``) and the value is re-validated at
        the cache layer so any future Pydantic ordering change (v3) that
        landed a raw env string here would not bypass H8 sanitization.

        Review pass-2 PM13: emit an ``info`` log when minting fresh so an
        operator can correlate an upstream-reject WARNING with the chosen
        substitute trace_id (8-char preview only — no full UUID in log).
        """
        log = structlog.get_logger(__name__)
        # Review pass-2 PM6 / PM12 — explicit narrowing + defensive re-validate.
        if self.trace_id is not None and is_valid_trace_id(self.trace_id):
            resolved: str = self.trace_id
        else:
            resolved = new_uuid7()
            # Review pass-2 PM13 / pass-3 TM2 — use shared helper.
            log.info(
                "worker_trace_id_minted_fresh",
                value_preview=_safe_trace_preview(resolved),
            )
        self._resolved_trace_id = resolved
        # Review pass-2 PM5 — eager session_id / worker_id resolution.
        self._resolved_session_id = self.session_id or new_session_id()
        self._resolved_worker_id = self.worker_id or new_worker_id()

    # ------------------------------------------------------------------
    # Methods (Story 9.6 review pass-1 L3 — grouped after private attrs).
    # ------------------------------------------------------------------

    def resolve_session_id(self) -> str:
        """Return the eagerly-resolved ``session_id`` (review pass-2 PM5).

        ``model_post_init`` populates the cache at construction so this is a
        pure read; the narrowing raise mirrors :meth:`resolve_trace_id`.
        Review pass-3 TH6: error message names the concrete class so the
        caller can tell exactly which Settings subclass tripped the invariant.
        """
        if self._resolved_session_id is None:
            raise RuntimeError(
                f"model_post_init must have populated _resolved_session_id "
                f"(cls={type(self).__name__})"
            )
        return self._resolved_session_id

    def resolve_worker_id(self) -> str:
        """Return the eagerly-resolved ``worker_id`` (review pass-2 PM5).

        Review pass-3 TH6: error message names the concrete class.
        """
        if self._resolved_worker_id is None:
            raise RuntimeError(
                f"model_post_init must have populated _resolved_worker_id "
                f"(cls={type(self).__name__})"
            )
        return self._resolved_worker_id

    def resolve_trace_id(self) -> str:
        """Return the validated ``trace_id`` (Story 9.6).

        Review pass-1 H4 + M1: ``model_post_init`` eagerly resolves the value
        at construction so this method is a pure read. The narrowing assert
        (review pass-1 M1) keeps mypy --strict happy without coercing the
        type at call sites.

        Review pass-3 TH6: error message names the concrete class so the
        caller can tell exactly which Settings subclass tripped the invariant
        (matches TH1's orchestrator-side resolver).
        """
        # ``model_post_init`` guarantees this is non-None for any
        # successfully-constructed instance — narrow the Optional for mypy.
        if self._resolved_trace_id is None:
            raise RuntimeError(
                f"model_post_init must have populated _resolved_trace_id "
                f"(cls={type(self).__name__})"
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
