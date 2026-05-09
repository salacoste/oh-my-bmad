"""Payload Pydantic models for all typed event schemas.

Relocated from ``registry_state.domain.event_types`` by Story 3.5.2.
The models are pure data classes — no side effects, no ``register()`` calls.
Registration stays in ``registry_state.domain.event_types`` because of a
circular-import constraint documented there.

All models use ``ConfigDict(frozen=True, strict=True, extra="forbid")``
matching the Story 2.1 discipline.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Shared regexes for ID validation (Story 2.10 review-pass tightening).
# ---------------------------------------------------------------------------

_SESSION_ID_PATTERN = r"^s-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_TASK_ID_PATTERN = r"^t-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_WORKER_ID_PATTERN = r"^w-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"


class TaskCreatedPayload(BaseModel):
    """Payload for the ``task.created`` event.

    Story 2.9 F7+F9: ``title`` (when present) is bounded to 512 chars; ``repo``
    and ``hint`` are optional creation-time inputs surfaced from the HTTP API.
    All three fields default to ``None`` so existing emit-sources that only
    pass ``task_id`` continue to work unchanged.

    Story 3.9 AC-1 / AC-11 — additive minor bump (1.0.0 → 1.1.0): ``chat_id``
    and ``reply_to_message_id`` carry the Telegram thread binding so outbound
    sinks can deliver progress events to the originating thread (FR13). Both
    are ``int | None`` (Telegram chat ids are negative for supergroups; the
    type is ``int``, NOT ``PositiveInt``). The single payload class is
    registered under both ``1.0.0`` and ``1.1.0`` per the same-model
    contract — pre-3.9 events deserialize cleanly with the new fields
    defaulting to ``None`` (additive-only NFR-M3).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str
    title: str | None = Field(default=None, max_length=512)
    repo: str | None = Field(default=None, max_length=2048)
    hint: str | None = Field(default=None, max_length=4096)
    # Story 3.9: Telegram thread binding (FR13).
    # M13: chat_id=0 rejected; L20: explicit BigInteger bounds.
    chat_id: int | None = Field(default=None, ge=-(2**63), le=(2**63) - 1)
    # M13: reply_to_message_id must be strictly positive (Telegram msg IDs ≥ 1).
    reply_to_message_id: int | None = Field(default=None, gt=0)

    @field_validator("chat_id")
    @classmethod
    def _chat_id_not_zero(cls, v: int | None) -> int | None:
        if v == 0:
            raise ValueError("chat_id must not be 0 — Telegram never uses chat_id=0")
        return v


class TaskPlanningStartedPayload(BaseModel):
    """Payload for the ``task.planning.started`` event."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str


class PlanStep(BaseModel):
    """A single step in a structured plan (Story 5.11 / FR2)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    step: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=500)


class TaskPlanReadyPayload(BaseModel):
    """Payload for the ``task.plan.ready`` event.

    Story 5.11 — additive minor bump (1.0.x → 1.1.0): ``plan`` and
    ``estimated_steps`` carry the structured step list so the Telegram
    plan-ready renderer can show individual steps. Both default to empty/zero
    so pre-5.11 events deserialize cleanly (NFR-M3 additive-only).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str
    plan_summary: str
    plan: tuple[PlanStep, ...] = Field(default=())
    estimated_steps: int = Field(default=0, ge=0)


class TaskExecutionStartedPayload(BaseModel):
    """Payload for the ``task.execution.started`` event."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str
    session_id: str


class TaskStepCompletedPayload(BaseModel):
    """Payload for the ``task.step.completed`` event (Story 5.12 / FR3+FR31)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64)
    step: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=500)
    output_summary: str = Field(max_length=2000)


class TaskBlockerRaisedPayload(BaseModel):
    """Payload for the ``task.blocker_raised`` event.

    Story 3.11 AC-1 — additive minor bump (1.0.x → 1.1.0): three optional
    FR15 fields (``blocked_since``, ``last_event``, ``last_action``) are
    added so the Telegram blocker-notification renderer can include
    blocked-since timestamp, the most recent event before the blocker,
    and the most recent agent action. All three default to ``None`` so
    legacy v1.0.x events deserialize cleanly. The single payload class
    is registered under ``1.0.0``, ``1.0.1``, and ``1.1.0`` (additive-only
    NFR-M3).

    Story 3.10 H3 carry-forward — model-boundary string validators:
    ``task_id`` (1..64 chars) and ``reason`` (1..2000 chars) gain explicit
    ``min_length`` / ``max_length`` validators matching the cap pattern set
    on :class:`TaskApprovalRequestedPayload`. ``last_event`` is bounded to
    1..128 chars (event-type registry convention); ``last_action`` is
    bounded to 1..2000 chars (same shape as approval ``action``). Empty
    strings on the optional fields would render a useless ``Last event:``
    line with a trailing space — Story 3.11 review H7 closes that gap by
    adding ``min_length=1`` to both.

    Story 3.11 review H8: ``blocked_since`` is typed
    :class:`pydantic.AwareDatetime` so naive timestamps (``.isoformat()``
    omits the offset suffix → operator sees ambiguous "12:00:00" with no
    timezone) are rejected at the payload boundary.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)
    # Story 3.11 — optional FR15 fields (additive, schema 1.1.0).
    # H8: AwareDatetime — naive datetimes rejected at the model boundary.
    blocked_since: AwareDatetime | None = None
    # H7: min_length=1 — empty string produces a useless ``Last event:`` line.
    last_event: str | None = Field(default=None, min_length=1, max_length=128)
    last_action: str | None = Field(default=None, min_length=1, max_length=2000)


class TaskSummaryEmittedPayload(BaseModel):
    """Payload for the ``task.summary_emitted`` event."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str
    summary: str


class PreCheckOutcome(BaseModel):
    """Outcome of a single pre-check (lint / types / unit / integration).

    Story 3.10 AC-2: ``passed`` and ``total`` are non-negative integers (a 0/0
    result is technically valid — renders as ``0/0``). ``status`` is derived
    semantically by the emitter; the renderer just shows it.

    Story 3.10 review-pass tightening:

    * **H4** — cross-field invariant: ``passed <= total`` (a check cannot pass
      more cases than it ran). Without this, the renderer would print
      nonsense like ``999/3 (failed)``.
    * **H5** — semantic invariant on ``status``: ``"pass"`` requires
      ``passed == total``; ``"fail"`` requires ``passed < total``.
      ``"skipped"`` and ``"error"`` (M13) are state-of-the-check values and
      have no count constraint.
    * **M13** — ``status`` widened to include ``"skipped"`` (env unavailable)
      and ``"error"`` (the check itself crashed). Renderer maps
      ``skipped → ⏭️``, ``error → ⚠️``.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    passed: int = Field(ge=0)
    total: int = Field(ge=0)
    status: Literal["pass", "fail", "skipped", "error"]

    @model_validator(mode="after")
    def _check_passed_le_total(self) -> PreCheckOutcome:
        if self.passed > self.total:
            raise ValueError(f"passed ({self.passed}) cannot exceed total ({self.total})")
        return self

    @model_validator(mode="after")
    def _check_status_count_consistency(self) -> PreCheckOutcome:
        # H5: only enforce the count invariant for the binary pass/fail
        # outcomes. "skipped" and "error" (M13) are check-state values that
        # carry no count semantics.
        if self.status == "pass" and self.passed != self.total:
            raise ValueError(
                f"status='pass' requires passed == total; got "
                f"passed={self.passed}, total={self.total}"
            )
        if self.status == "fail" and self.passed >= self.total:
            raise ValueError(
                f"status='fail' requires passed < total; got "
                f"passed={self.passed}, total={self.total}"
            )
        return self


class PreCheckResults(BaseModel):
    """Aggregate pre-check results for an approval request (Story 3.10 AC-2).

    Each individual check is optional — the renderer omits the line when the
    corresponding field is ``None``.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    lint: PreCheckOutcome | None = None
    types: PreCheckOutcome | None = None
    unit: PreCheckOutcome | None = None
    integration: PreCheckOutcome | None = None


class DiffSummary(BaseModel):
    """Diff summary for an approval request (Story 3.10 AC-3).

    Renders as ``<files> files, +<insertions>, -<deletions>``. All three
    fields are required when ``DiffSummary`` is non-None — the emitter
    either populates the whole struct or leaves it ``None``.

    Story 3.10 review-pass L9: per-field upper bound ``<= 10**9`` so a
    buggy diff parser shipping ``insertions=2**63 - 1`` is rejected at the
    payload boundary instead of rendering a 19-digit number.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    files: int = Field(ge=0, le=10**9)
    insertions: int = Field(ge=0, le=10**9)
    deletions: int = Field(ge=0, le=10**9)


#: Story 3.10 H6 — per-element bounds for entries in
#: :attr:`TaskApprovalRequestedPayload.accepted_commands`. Each command must
#: be a non-empty string of at most 200 chars (matches the renderer's
#: per-bullet visual budget). Renderer keeps its own caps as
#: defense-in-depth.
AcceptedCommand = Annotated[str, Field(min_length=1, max_length=200)]


class TaskApprovalRequestedPayload(BaseModel):
    """Payload for the ``task.approval_requested`` event.

    Story 3.10 AC-1 — additive minor bump (1.0.0 → 1.1.0): four optional
    FR14 fields (``risk_class``, ``pre_check_results``, ``diff_summary``,
    ``accepted_commands``) are added so the Telegram approval-request
    renderer can include risk class, pre-check status, diff summary, and
    the exact commands accepted. All four default to ``None`` so legacy
    v1.0.0 events (Story 2.8 emit shape) deserialize cleanly. The single
    payload class is registered under both ``1.0.0`` and ``1.1.0``
    (additive-only NFR-M3).

    Story 3.10 review-pass tightening:

    * **H3** — ``task_id`` (1..64 chars), ``action`` (1..2000 chars), and
      ``justification`` (1..10_000 chars) gain explicit ``min_length`` /
      ``max_length`` validators. Caps reasonably above the renderer's
      3500-char total cap so wire-level validation fails fast on bad
      inputs from upstream emitters (Story 6.4).
    * **H6** — ``accepted_commands`` gains model-level bounds: ``<= 20``
      entries (renderer trims to 10 — model bound is intentionally looser
      so future stories can negotiate without a schema bump), each entry
      ``1..200`` chars via :data:`AcceptedCommand`. Defends consumers
      beyond the renderer (audit log, registry-API echo).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=2000)
    justification: str = Field(min_length=1, max_length=10_000)
    # Story 3.10 — optional FR14 fields (additive, schema 1.1.0).
    risk_class: Literal["low", "medium", "high"] | None = None
    pre_check_results: PreCheckResults | None = None
    diff_summary: DiffSummary | None = None
    accepted_commands: list[AcceptedCommand] | None = Field(default=None, max_length=20)


class TaskCompletedPayload(BaseModel):
    """Payload for the ``task.completed`` event.

    Story 3.12 AC-1 — additive minor bump (1.0.x → 1.1.0): seven optional
    FR9 fields plus tightened model-boundary validators on the existing
    fields. The seven new fields (``pr_number``, ``pr_branch``,
    ``files_changed``, ``lines_added``, ``lines_removed``, ``tests_added``,
    ``ci_state``, ``blockers_count``) all default to ``None`` so legacy
    v1.0.x events deserialize cleanly under v1.1.0 (NFR-M3 additive-only).

    Story 3.10 H3 carry-forward — model-boundary string validators:
    ``task_id`` (1..64 chars), ``summary`` (1..2000 chars), and ``pr_url``
    (1..500 chars) gain explicit ``min_length`` / ``max_length`` validators
    matching the cap pattern set on
    :class:`TaskApprovalRequestedPayload` / :class:`TaskBlockerRaisedPayload`.

    Story 3.10 review L9 carry-forward — per-field upper bounds on the new
    integer counters defend against buggy diff-parser overflow:
    ``pr_number``, ``lines_added``, ``lines_removed`` cap at ``10**9``;
    ``files_changed``, ``tests_added``, ``blockers_count`` cap at ``10**6``
    (more than enough for any reasonable PR; defends against integer-overflow
    injection). ``pr_branch`` capped at 255 chars (git ref-name length limit).

    Story 3.12 review M14 — counter-cap semantic distinction. The 1M cap on
    file-level counters (``files_changed`` / ``tests_added`` / ``blockers_count``)
    is intentional — even a monorepo-wide refactor rarely touches >1M files,
    and tests-added / blockers-raised counts in a single ``task.completed``
    are bounded by per-task scope. The 1B cap on line-level counters
    (``lines_added`` / ``lines_removed`` / ``pr_number``) is the broader
    defense-in-depth bound matching :class:`DiffSummary` (Story 3.10 L9).
    A future story producing legitimate >1M-file events should bump the
    file-level cap explicitly rather than relying on overflow.

    Story 3.12 review M2 — ``pr_url`` constrained to ``http://`` /
    ``https://`` schemes via ``pattern=r"^https?://"``. Defense against
    operator-supplied / upstream-emitted ``javascript:`` / ``data:`` URLs
    that Telegram link-affordance heuristics might surface as clickable
    even after HTML escape.

    Story 3.12 review L4 — schema-version registration ordering. Three
    versions (1.0.0, 1.0.1, 1.1.0) of the same model are registered. The
    ``schema_registry.register`` contract is "same model for same key is a
    no-op idempotent op"; insertion order is irrelevant for the per-version
    lookup. If a future ``1.0.2`` lands (e.g. a hot-fix forced into the v1.0
    line) it must be registered explicitly with the THEN-CURRENT model — the
    1.1.0 entry is unaffected. Versions form a partial map; there is no
    "default-to-latest" behavior to worry about.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64)
    summary: str = Field(min_length=1, max_length=2000)
    # Story 3.12 review M2: scheme-constrained — ``http(s)://`` only. Other
    # schemes (``javascript:`` / ``data:`` / ``file:``) are rejected at the
    # model boundary, not at the renderer.
    pr_url: str | None = Field(default=None, min_length=1, max_length=500, pattern=r"^https?://")
    # Story 3.12 — optional FR9 fields (additive, schema 1.1.0).
    # Story 3.12 review L5: ``ge=1`` matches GitHub PR numbering (PRs start at
    # 1). Other VCS systems (Gitea, Forgejo, GitLab MRs) also use ``>=1`` so
    # this constraint is portable in practice; if a future host uses 0-based
    # numbering, the cap can relax with an explicit migration comment.
    pr_number: int | None = Field(default=None, ge=1, le=10**9)
    pr_branch: str | None = Field(default=None, min_length=1, max_length=255)
    files_changed: int | None = Field(default=None, ge=0, le=10**6)
    lines_added: int | None = Field(default=None, ge=0, le=10**9)
    lines_removed: int | None = Field(default=None, ge=0, le=10**9)
    tests_added: int | None = Field(default=None, ge=0, le=10**6)
    ci_state: Literal["green", "red", "unknown"] | None = None
    blockers_count: int | None = Field(default=None, ge=0, le=10**6)
    # Story 5.15 — cumulative token usage for observability (FR44).
    token_usage: int | None = Field(default=None, ge=0, le=10**9)


# ---------------------------------------------------------------------------
# Story 2.10 — failure-detection payload models (FR24a, NFR-R5).
# ---------------------------------------------------------------------------


class ServiceCrashedPayload(BaseModel):
    """Payload for the ``service.crashed`` event.

    Emitted when a supervised process exits with a non-zero exit code.

    Field rules (post-2.10 review-pass):

    * ``service``: 1..128 chars (logical service name, e.g. ``worker-wrapper``).
    * ``exit_code``: any integer except ``0`` — ``service.crashed`` MUST NOT
      be emitted for clean exits (validator rejects ``0`` with a clear
      message).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    service: str = Field(min_length=1, max_length=128)
    exit_code: int = Field(...)

    @field_validator("exit_code")
    @classmethod
    def _exit_code_nonzero(cls, v: int) -> int:
        if v == 0:
            raise ValueError(
                "exit_code must be non-zero for service.crashed (clean exits "
                "do not constitute a crash; got exit_code=0)"
            )
        return v


class SessionHeartbeatTimeoutPayload(BaseModel):
    """Payload for the ``session.heartbeat_timeout`` event.

    Emitted when a session's last heartbeat is older than 2× the configured
    heartbeat interval (strict ``>`` boundary — see :class:`HeartbeatMonitor`).

    Field rules (post-2.10 review-pass):

    * ``session_id``: must match ``^s-<uuidv7>$``.
    * ``task_id``: must match ``^t-<uuidv7>$``.
    * ``last_heartbeat_at``: :class:`pydantic.AwareDatetime` — naive timestamps
      are rejected at the payload boundary.
    * ``timeout_threshold_s``: ``> 0`` and finite (``NaN``/``inf`` rejected).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)
    task_id: str = Field(min_length=1, pattern=_TASK_ID_PATTERN)
    last_heartbeat_at: AwareDatetime
    timeout_threshold_s: float = Field(gt=0, allow_inf_nan=False)

    @field_validator("last_heartbeat_at")
    @classmethod
    def _last_heartbeat_utc(cls, v: AwareDatetime) -> AwareDatetime:
        if v.utcoffset() != timedelta(0):
            raise ValueError(
                f"last_heartbeat_at must be UTC (zero offset); got utcoffset={v.utcoffset()!r}"
            )
        return v


class SinkDeliveryFailedPayload(BaseModel):
    """Payload for the ``sink.delivery_failed`` event.

    Emitted when a sink (e.g. Telegram) has accumulated ``failure_threshold``
    consecutive delivery failures.

    Field rules (post-2.10 review-pass):

    * ``sink_name``: 1..128 chars.
    * ``consecutive_failures``: ``>= 1`` (the gate fires at threshold; emit
      MUST NOT be called for zero-failure ticks).
    * ``last_error``: optional, ``<= 4096`` chars. Defense-in-depth secret
      redaction is applied at the emit site (:func:`emit_sink_delivery_failed`
      runs ``_redact_last_error`` before constructing the payload), so any
      tokens that slip past caller sanitization are masked. Callers SHOULD
      still sanitize.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    sink_name: str = Field(min_length=1, max_length=128)
    consecutive_failures: int = Field(ge=1)
    last_error: str | None = Field(default=None, max_length=4096)


class TaskStopRequestedPayload(BaseModel):
    """Payload for the ``task.stop_requested`` event.

    Emitted when an operator (Telegram, console, etc.) requests that an
    in-flight task stop. Materializer state transition (e.g. ``tasks.status =
    "stopped"``) is wired in Epic 3.

    Field rules (post-2.10 review-pass):

    * ``task_id``: must match ``^t-<uuidv7>$``.
    * ``actor_id``: 1..128 chars (free-form operator identifier — e.g.
      ``telegram:12345678`` or ``console``).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, pattern=_TASK_ID_PATTERN)
    actor_id: str = Field(min_length=1, max_length=128)


class TaskSelfRecoveredPayload(BaseModel):
    """Payload for the ``task.self_recovered`` event (FR16).

    Synthesized by the clawhip-daemon proactive-summary code when a task's
    event log contains a ``session.reconnecting`` + ``task.execution.resumed``
    pair emitted overnight (between 00:00 and the morning completion summary).
    Story 3.13 ships the model + renderer; the synthesis logic is Story 7.9.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64)
    recovered_at: AwareDatetime
    events_replayed: int = Field(ge=0, le=10**6)
    replay_duration_ms: int = Field(ge=0, le=10**9)


class SecretAccessedPayload(BaseModel):
    """Payload for the ``secret.accessed`` audit event (FR42 / NFR-S3).

    The secret VALUE is NEVER included — only the metadata identifying
    which secret was read, by which actor, and at what scope. The actor
    identity is carried on the envelope's ``actor`` field; this payload
    records the *what* (``secret_name``) and the *kind of access*
    (``scope``).

    Story 2.16 ships the infrastructure (this payload + the
    :class:`secret_hygiene.AuditedSecret` wrapper). Phase 1 only supports
    ``scope="read"``; future stories may add ``"rotated"`` or
    ``"exposed"`` once those workflows exist.

    Field rules:

    * ``secret_name``: 1..128 chars (stable identifier — e.g.
      ``"anthropic_api_key"``, ``"telegram_bot_token"``).
    * ``scope``: literal ``"read"`` (Phase 1).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    secret_name: str = Field(min_length=1, max_length=128)
    scope: Literal["read"] = "read"


# ---------------------------------------------------------------------------
# Story 5.2 — session lifecycle payload models (FR24a).
# ---------------------------------------------------------------------------


class SessionStartedPayload(BaseModel):
    """Payload for the ``session.started`` event.

    Emitted when a worker starts and registers itself with the session registry.

    Field rules:

    * ``session_id``: must match ``^s-<uuidv7>$``.
    * ``worker_id``: must match ``^w-<uuidv7>$``.
    * ``task_id``: optional, must match ``^t-<uuidv7>$`` when present.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)
    worker_id: str = Field(min_length=1, pattern=_WORKER_ID_PATTERN)
    task_id: str | None = Field(default=None, min_length=1, pattern=_TASK_ID_PATTERN)


class SessionHeartbeatPayload(BaseModel):
    """Payload for the ``session.heartbeat`` event.

    Emitted periodically while the worker is running (default every 30 s).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)


class SessionFinishedPayload(BaseModel):
    """Payload for the ``session.finished`` event.

    Emitted on graceful shutdown (SIGTERM/SIGINT) before the process exits.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)


# Schema version constant for telegram.rejected — single source of truth
# so middleware.py and future consumers don't hardcode the string.
TELEGRAM_REJECTED_SCHEMA_VERSION = "1.0.0"


class TelegramRejectedPayload(BaseModel):
    """Payload for the ``telegram.rejected`` event (FR11 / NFR-S4 audit trail).

    Emitted by the telegram-gateway's allowlist outer middleware when an
    inbound Telegram update fails the allowlist check. PII surface is
    intentionally minimal: ``user_id`` plus a structured ``reason`` only.
    No message content, no username, no chat metadata.

    Field rules:

    * ``user_id``: ``>= 0``. Real Telegram user ids are positive integers
      (``>= 1``); ``0`` is reserved as the sentinel for events lacking a
      ``from_user`` (e.g. ``my_chat_member``, ``poll`` updates) which
      the middleware rejects defensively per Story 3.2 AC-7.
    * ``reason``: ``"not_in_allowlist"`` (default — user id known but not
      whitelisted) or ``"no_from_user"`` (event arrived without a sender
      identity; rejected via the ``user_id=0`` sentinel).

    Cross-field invariant (L6):
    * ``user_id=0`` requires ``reason="no_from_user"``
    * ``user_id>0`` requires ``reason!="no_from_user"``
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    user_id: int = Field(ge=0)
    reason: Literal["not_in_allowlist", "no_from_user"] = "not_in_allowlist"

    @model_validator(mode="after")
    def _check_user_id_reason_consistency(self) -> TelegramRejectedPayload:
        if self.user_id == 0 and self.reason != "no_from_user":
            raise ValueError("user_id=0 requires reason='no_from_user'")
        if self.user_id > 0 and self.reason == "no_from_user":
            raise ValueError("reason='no_from_user' requires user_id=0")
        return self


# ---------------------------------------------------------------------------
# Story 5.5 — agent.reasoning.* breadcrumb payload (FR17b, NFR-O6).
# ---------------------------------------------------------------------------


class AgentReasoningBreadcrumbPayload(BaseModel):
    """Payload for ``agent.reasoning.*`` breadcrumb events (FR17b, NFR-O6).

    Emitted when the worker extracts reasoning content from Claude Code's
    ``thinking`` or ``text`` content blocks in stream-json output.  All text
    fields are sanitized through the secret-hygiene scanner before emission;
    if secrets are detected the text is suppressed.

    Field rules:

    * ``session_id``: must match ``^s-<uuidv7>$``.
    * ``subtype``: one of three classification literals.
    * ``text``: sanitized reasoning text.  Empty string when suppressed.
    * ``suppressed``: ``True`` when secret-hygiene scanner detected sensitive
      content and the text was replaced.
    * ``tool_name``: the tool_use name when subtype is ``tool_call_rationale``.
    * ``raw_length``: original text length before sanitization (observability).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)
    subtype: Literal["plan_drafted", "tool_call_rationale", "step_summary"]
    text: str = Field(max_length=50_000)
    suppressed: bool = False
    tool_name: str | None = Field(default=None, min_length=1, max_length=64)
    raw_length: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_text_suppressed_consistency(self) -> AgentReasoningBreadcrumbPayload:
        if not self.suppressed and self.text == "":
            msg = "Non-suppressed breadcrumb must have non-empty text"
            raise ValueError(msg)
        return self


class FileEditedPayload(BaseModel):
    """Payload for ``file.edited`` events (FR30, NFR-R2).

    Emitted when the worker performs an atomic file edit or write.  Captures
    the file path, which Claude Code tool triggered the edit, line change
    counts, and whether secrets were detected (which aborts the write).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN)
    file_path: str = Field(min_length=1, max_length=4096)
    tool_name: Literal["Write", "Edit"]
    lines_added: int = Field(ge=0, le=10**9)
    lines_removed: int = Field(ge=0, le=10**9)
    secrets_detected: bool = False


class TaskBudgetExceededPayload(BaseModel):
    """Payload for the ``task.budget_exceeded`` event (FR44 / NFR-P5).

    Emitted when cumulative token usage for a task exceeds the configured
    ``task_token_budget`` ceiling.  The task is blocked pending extension
    approval — ``task.completed`` is NOT emitted in this path.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=64)
    token_limit: int = Field(gt=0)
    tokens_used: int = Field(gt=0)
    step: int = Field(ge=1)


class Tier3ActionAttemptedPayload(BaseModel):
    """Payload for the ``tier3.action_attempted`` event (FR38 / Story 6.2).

    Emitted when a Tier-3 capability check is performed.  Records whether
    the action was accepted or denied, with a reason on denial.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    action: str = Field(min_length=1, max_length=2000)
    task_id: str = Field(min_length=1, max_length=64)
    accepted: bool
    reason: str | None = Field(default=None, max_length=4096)


__all__ = [
    "TELEGRAM_REJECTED_SCHEMA_VERSION",
    "AcceptedCommand",
    "AgentReasoningBreadcrumbPayload",
    "DiffSummary",
    "FileEditedPayload",
    "PlanStep",
    "PreCheckOutcome",
    "PreCheckResults",
    "SecretAccessedPayload",
    "ServiceCrashedPayload",
    "SessionFinishedPayload",
    "SessionHeartbeatPayload",
    "SessionHeartbeatTimeoutPayload",
    "SessionStartedPayload",
    "SinkDeliveryFailedPayload",
    "TaskApprovalRequestedPayload",
    "TaskBlockerRaisedPayload",
    "TaskBudgetExceededPayload",
    "TaskCompletedPayload",
    "TaskCreatedPayload",
    "TaskExecutionStartedPayload",
    "TaskPlanReadyPayload",
    "TaskPlanningStartedPayload",
    "TaskSelfRecoveredPayload",
    "TaskStepCompletedPayload",
    "TaskStopRequestedPayload",
    "TaskSummaryEmittedPayload",
    "TelegramRejectedPayload",
    "Tier3ActionAttemptedPayload",
]
