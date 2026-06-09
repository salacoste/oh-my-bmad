"""Orchestrator-adapter settings — MCP, OMC, GitHub, budget (Stories 5.10, 5.14, 5.15).

Story 9.6 review pass-3 TH1: ``trace_id`` mirrors the worker-side
defense-in-depth pattern (validator + post_init eager resolve +
``resolve_trace_id`` accessor + alias-fallthrough + escaped log preview).
"""

from __future__ import annotations

import os
from typing import Any, ClassVar

import structlog
from events.envelope import is_valid_trace_id
from events.ids import new_session_id, new_uuid7, new_worker_id
from pydantic import AliasChoices, Field, PrivateAttr, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Story 9.6 review pass-3 TH1 / TM2 — shared module-level constants.
_TRACE_ID_ALIASES: tuple[str, ...] = (
    "ORCHESTRATOR_TRACE_ID",
    "OMB_ORCHESTRATOR_TRACE_ID",
    "OMB_TRACE_ID",
)


def _safe_trace_preview(tid: str) -> str:
    """Return an 8-char bounded preview suitable for structured log fields.

    Story 9.6 review pass-3 TM2: extracted helper, reused across config /
    main / __main__ log call sites.
    """
    return tid[:8] + "..."


class OrchestratorSettings(BaseSettings):
    """Configuration for the orchestrator-adapter service.

    MCP servers are spawned as subprocesses via ``command + args``.
    OMC path points to the vendored ``upstream/omc/`` directory.
    Override via environment variables prefixed with ``ORCHESTRATOR_``.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="ORCHESTRATOR_",
        populate_by_name=True,
    )

    task_registry_command: str = "python"
    task_registry_args: list[str] = ["-m", "task_registry_mcp"]
    # Phase 10 / ADR-0022: streamable-http URL for task-registry. When set,
    # streamable-http transport is used instead of stdio. Empty string (default)
    # means stdio mode — backward compatible with Phase 9.
    task_registry_url: str = ""

    session_registry_command: str = "python"
    session_registry_args: list[str] = ["-m", "session_registry_mcp"]
    # Phase 10 / ADR-0022: streamable-http URL for session-registry.
    session_registry_url: str = ""

    clawhip_bridge_command: str = "python"
    clawhip_bridge_args: list[str] = ["-m", "clawhip_bridge_mcp"]
    # Phase 10 / ADR-0022: streamable-http URL for clawhip-bridge.
    clawhip_bridge_url: str = ""

    # Latent scaffold: shared SQLite path (see worker-wrapper config.py).
    registry_db_path: str = ""
    ready_file_path: str = "/tmp/ready"

    actor_id: str = ""
    session_id: str = ""
    omc_path: str = "upstream/omc"
    omc_timeout_s: float = Field(default=120.0, gt=0)
    poll_interval_s: float = Field(default=5.0, gt=0)

    # Story 5.14 — GitHub PR draft creation
    github_token: SecretStr = SecretStr("")
    github_api_base_url: str = "https://api.github.com"
    github_timeout_s: float = Field(default=10.0, gt=0)
    github_base_branch: str = "main"

    # Story 5.15 — Per-task budget enforcement (FR44). 0 disables enforcement.
    # Story 12.4: this is now the LEGACY/lowest-precedence token-ceiling default
    # (ORCHESTRATOR_TASK_TOKEN_BUDGET). The new operator-facing default below
    # (OMB_DEFAULT_TASK_BUDGET_TOKENS) takes precedence over it; a per-task
    # Task-row ``budget_token_limit`` takes precedence over both.
    task_token_budget: int = Field(default=50_000, ge=0)

    # Story 12.4 (FR68a / D3) — operator-facing per-task budget default. Sits
    # ABOVE the legacy ``task_token_budget`` and BELOW a per-task row value in
    # the precedence chain: per-task row > OMB_DEFAULT_TASK_BUDGET_TOKENS >
    # ORCHESTRATOR_TASK_TOKEN_BUDGET. Read via an explicit ``validation_alias``
    # so it escapes the ``ORCHESTRATOR_`` env prefix and matches the .env name.
    # ``None`` (unset) means "fall through to the legacy default".
    default_task_budget_tokens: int | None = Field(
        default=None,
        gt=0,
        # Story 12.4 review (MEDIUM): use ONLY the intended OMB_ env name. A
        # redundant field-name alias ("default_task_budget_tokens") would make
        # pydantic-settings ALSO match an unprefixed DEFAULT_TASK_BUDGET_TOKENS
        # env var — an unintended/ghost surface. populate_by_name=True still lets
        # the field be set by its python name in code/tests.
        validation_alias=AliasChoices("OMB_DEFAULT_TASK_BUDGET_TOKENS"),
        description=(
            "Operator-facing default per-task token ceiling (OMB_DEFAULT_TASK_BUDGET_TOKENS, "
            "Story 12.4 / FR68a). Precedence: per-task Task-row budget_token_limit > "
            "OMB_DEFAULT_TASK_BUDGET_TOKENS > ORCHESTRATOR_TASK_TOKEN_BUDGET (legacy)."
        ),
    )

    # Story 12.3c (FR68) — bounded wait, in seconds, for an operator budget
    # override to raise the per-task ceiling after the in-memory BudgetTracker
    # breaches. On breach, process_task emits ``task.budget_exceeded`` then
    # polls task-registry for a raised ``budget_token_limit`` until this
    # MONOTONIC deadline elapses; if the ceiling is raised it re-arms the
    # tracker and RESUMES the step loop, otherwise it fails-closed (terminates)
    # exactly as the pre-12.3c behavior. Default matched to the worker-side
    # grace window (worker ``budget_grace_window_s`` default 5.0) but slightly
    # larger so the orchestrator does not give up before a pre-staged override
    # lands. Read via an explicit ``validation_alias`` so it escapes the
    # ``ORCHESTRATOR_`` env prefix and matches the operator-facing OMB_ name.
    override_wait_s: float = Field(
        default=30.0,
        gt=0,
        le=300,
        validation_alias=AliasChoices("OMB_ORCH_OVERRIDE_WAIT_S"),
        description=(
            "Bounded monotonic wait (seconds) for an operator budget override to "
            "raise the per-task ceiling after a BudgetTracker breach before the "
            "orchestrator fails-closed (OMB_ORCH_OVERRIDE_WAIT_S, Story 12.3c / "
            "FR68). On a raised budget_token_limit the tracker re-arms and the "
            "step loop resumes; on timeout the loop breaks (terminate)."
        ),
    )

    # Story 12.3c (FR68) — poll cadence (seconds) for the override-wait loop;
    # uses ``await asyncio.sleep`` so it never blocks the event loop.
    override_poll_interval_s: float = Field(
        default=1.0,
        gt=0,
        le=60,
        validation_alias=AliasChoices("OMB_ORCH_OVERRIDE_POLL_INTERVAL_S"),
        description=(
            "Poll cadence (seconds) for the Story 12.3c override-wait loop "
            "(OMB_ORCH_OVERRIDE_POLL_INTERVAL_S). Re-reads the task's "
            "budget_token_limit via task-registry MCP each interval."
        ),
    )

    # Story 9.6 review pass-2 PH0 + pass-3 TH1 — orchestrator-adapter is the
    # REAL spawner of worker-wrapper (via OMC).  ``OMCRunner`` propagates
    # this trace_id to the child subprocess as ``OMB_TRACE_ID`` so downstream
    # workers resolve it via ``AliasChoices``.  Accepts the same three
    # env-var names as the worker side (canonical first); the validator and
    # ``model_post_init`` mirror the worker-side defense pattern.
    trace_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("trace_id", *_TRACE_ID_ALIASES),
        description=(
            "Trace_id supplied by the spawning service for this orchestrator-adapter "
            "invocation. Set via ORCHESTRATOR_TRACE_ID (canonical), "
            "OMB_ORCHESTRATOR_TRACE_ID, or OMB_TRACE_ID env vars (all three are "
            "accepted via AliasChoices). Story 9.6 / FR59 / NFR-O7. Must match "
            "Story 9.1 contract (UUIDv7 or 'tg:<update_id>'); if absent or invalid, "
            "the adapter mints a fresh UUIDv7 with a WARNING log. Threaded through "
            "OMCRunner._spawn as OMB_TRACE_ID env var AND as caller_trace_id on "
            "every clawhip-bridge MCP tool call."
        ),
    )

    # ------------------------------------------------------------------
    # Validators (Story 9.6 review pass-3 TH1 — mirror worker-side shape).
    # ------------------------------------------------------------------

    @field_validator("trace_id", mode="before")
    @classmethod
    def _validate_trace_id_shape(cls, value: object) -> str | None:
        """Per Story 9.6 AC2: present-but-invalid → log WARNING + return None
        (consumer mints fresh); absent → return None silently.

        Mirrors worker-side validator: empty/invalid canonical falls through to
        remaining aliases (PH1 alias-fallthrough); each visited invalid alias
        emits a per-fallback ``trace_id_alias_invalid`` WARNING (TM3); the
        warning preview uses ``repr()`` so CRLF / NULL / ANSI / ZWJ / RTL
        override / control chars are escaped.
        """
        if value is None:
            return None
        log = structlog.get_logger(__name__)

        def _try_fallback_aliases() -> str | None:
            for fallback_env in _TRACE_ID_ALIASES[1:]:
                candidate = os.environ.get(fallback_env, "")
                if not candidate:
                    continue
                if is_valid_trace_id(candidate):
                    # Story 9.6 review pass-3 TL1 — info log when fallback fires.
                    log.info(
                        "trace_id_alias_fallthrough_accepted",
                        alias_env=fallback_env,
                    )
                    return candidate
                # Story 9.6 review pass-3 TM3 — per-fallback invalid warning.
                log.warning(
                    "trace_id_alias_invalid",
                    alias_env=fallback_env,
                    value_preview=repr(candidate[:80]),
                )
            return None

        if value == "":
            fallback = _try_fallback_aliases()
            if fallback is not None:
                return fallback
            log.warning(
                "orchestrator_trace_id_invalid_will_mint_fresh",
                value_preview=repr(""),
                reason="empty_string",
            )
            return None
        if not isinstance(value, str) or not is_valid_trace_id(value):
            fallback = _try_fallback_aliases()
            if fallback is not None:
                return fallback
            preview_src = value if isinstance(value, str) else str(value)
            log.warning(
                "orchestrator_trace_id_invalid_will_mint_fresh",
                value_preview=repr(preview_src[:80]),
                reason="shape_mismatch",
            )
            return None
        return value

    # ------------------------------------------------------------------
    # Private attrs / cache (Story 9.6 review pass-3 TH1).
    # ------------------------------------------------------------------

    _resolved_actor_id: str | None = PrivateAttr(default=None)
    _resolved_session_id: str | None = PrivateAttr(default=None)
    _resolved_trace_id: str | None = PrivateAttr(default=None)
    _resolved_trace_id_source: str | None = PrivateAttr(default=None)

    # ------------------------------------------------------------------
    # Lifecycle hooks (Story 9.6 review pass-3 TH1).
    # ------------------------------------------------------------------

    def model_post_init(self, __context: Any) -> None:
        """Eagerly resolve trace_id and session_id at construction (mirrors worker-side H4).

        Story 9.6 review pass-3 TH1: ``resolve_trace_id()`` is a pure read after
        this point; no async race-window between concurrent readers.  Source
        ("env" vs "minted") is tracked in ``_resolved_trace_id_source`` so the
        ``ready`` log can advertise provenance (TM2).

        Story 5.17a: ``session_id`` is resolved eagerly following the same
        pattern as worker-wrapper — uses the configured value or mints a fresh
        one via ``new_session_id()``.
        """
        log = structlog.get_logger(__name__)
        if self.trace_id is not None and is_valid_trace_id(self.trace_id):
            self._resolved_trace_id = self.trace_id
            self._resolved_trace_id_source = "env"
        else:
            minted = new_uuid7()
            log.info(
                "orchestrator_trace_id_minted_fresh",
                value_preview=_safe_trace_preview(minted),
            )
            self._resolved_trace_id = minted
            self._resolved_trace_id_source = "minted"

        # Story 5.17a — eager session_id resolution (mirrors worker-wrapper).
        self._resolved_session_id = self.session_id or new_session_id()

    # ------------------------------------------------------------------
    # Methods (Story 9.6 review pass-3 TH1).
    # ------------------------------------------------------------------

    def resolve_actor_id(self) -> str:
        """Return ``actor_id`` or generate a new UUIDv7 if empty (cached)."""
        if self._resolved_actor_id is None:
            self._resolved_actor_id = self.actor_id or new_worker_id()
        return self._resolved_actor_id

    def resolve_session_id(self) -> str:
        """Return the eagerly-resolved ``session_id`` (Story 5.17a).

        ``model_post_init`` guarantees this is non-None for any
        successfully-constructed instance.
        """
        if self._resolved_session_id is None:
            raise RuntimeError(
                f"model_post_init must have populated _resolved_session_id "
                f"(cls={type(self).__name__})"
            )
        return self._resolved_session_id

    def resolve_trace_id(self) -> str:
        """Return the eagerly-resolved ``trace_id`` (TH1 + TH6).

        ``model_post_init`` guarantees this is non-None for any
        successfully-constructed instance; the narrowing raise mirrors the
        worker-side resolver and surfaces a typed RuntimeError instead of
        an ``assert`` (Story 9.4 S2 lesson — asserts are stripped under
        ``python -O``).  TH6: error message names the concrete class.
        """
        if self._resolved_trace_id is None:
            raise RuntimeError(
                f"model_post_init must have populated _resolved_trace_id "
                f"(cls={type(self).__name__})"
            )
        return self._resolved_trace_id

    # FC-P6-1 — Worker pool auto-scaling (config-gated, disabled by default).
    autoscale_enabled: bool = False
    autoscale_up_threshold: int = 3  # Scale up when queued tasks exceed this
    autoscale_down_threshold: int = 2  # Scale down when idle workers exceed this
    autoscale_min: int = 1  # Minimum worker count
    autoscale_max: int = 5  # Maximum worker count
    autoscale_poll_interval_s: float = Field(default=30.0, gt=0)  # Seconds between poll cycles
