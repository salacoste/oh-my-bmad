"""Payload models + schema-registry registrations for task event types.

Story 2.5 ships the first 4 event types. Story 2.8 extends with 4 more:
  - task.blocker_raised
  - task.summary_emitted
  - task.approval_requested
  - task.completed

Story 2.10 adds 4 failure-detection event types (FR24a, NFR-R5):
  - service.crashed
  - session.heartbeat_timeout
  - sink.delivery_failed
  - task.stop_requested

All models use ``ConfigDict(frozen=True, strict=True, extra="forbid")``
matching the Story 2.1 discipline. Registration calls are at module bottom
so the side-effect runs once on import (idempotent: same model for same key
is a no-op per Story 2.1's schema_registry.register contract).

**Story 2.10 review-pass tightening (post-1.0)**:

* ``ServiceCrashedPayload.exit_code`` rejects ``0`` via a ``@field_validator``
  (the docstring mandate: "non-zero" is now enforced).
* ``SinkDeliveryFailedPayload.consecutive_failures`` is bounded ``>= 1``;
  ``last_error`` is bounded to ``<= 4096`` chars.
* ``SessionHeartbeatTimeoutPayload.last_heartbeat_at`` is typed
  :class:`pydantic.AwareDatetime` so naive datetimes are rejected at the
  payload boundary (defense-in-depth on top of envelope-level enforcement).
* ``timeout_threshold_s`` rejects ``<= 0``, ``NaN`` and ``inf``.
* All ID-shaped fields carry length / regex constraints — ``session_id``
  must match ``s-<uuidv7>``, ``task_id`` ``t-<uuidv7>``; opaque-string
  IDs (``service``, ``actor_id``, ``sink_name``) are 1..128 chars.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Literal

from events.schema_registry import register
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


class TaskPlanReadyPayload(BaseModel):
    """Payload for the ``task.plan.ready`` event."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str
    plan_summary: str


class TaskExecutionStartedPayload(BaseModel):
    """Payload for the ``task.execution.started`` event."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str
    session_id: str


# ---------------------------------------------------------------------------
# Register all 4 event types with Story 2.1's schema_registry.
# Idempotent: re-registering the same model for the same key is a no-op.
# ---------------------------------------------------------------------------


class TaskBlockerRaisedPayload(BaseModel):
    """Payload for the ``task.blocker_raised`` event."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str
    reason: str


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
    """Payload for the ``task.completed`` event."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str
    summary: str
    pr_url: str | None = None


# ---------------------------------------------------------------------------
# Story 2.10 — failure-detection payload models (FR24a, NFR-R5).
#
# These 4 events are observability/signalling events; their state-transition
# handlers are deferred to later epics (Epic 3 for sink failures, Epic 5 for
# worker/session lifecycle). Story 2.10 ships only the typed-event
# infrastructure + emission primitives in
# ``registry_state.domain.failure_detection``.
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
# Register all event types with Story 2.1's schema_registry.
# Idempotent: re-registering the same model for the same key is a no-op.
# ---------------------------------------------------------------------------

register("task.created", "1.0.0", TaskCreatedPayload)
register("task.created", "1.0.1", TaskCreatedPayload)
# Story 3.9 AC-11 / H7: register 1.1.0 here alongside 1.0.0/1.0.1. A
# packages-side registration in events.schema_registry was attempted but
# triggers a circular import (events.__init__ → registry_state.__init__ →
# registry_state.adapters.event_log → events.EventEnvelope while the
# events package is still being initialized). Service-side registration is
# the single source of truth.
register("task.created", "1.1.0", TaskCreatedPayload)
register("task.planning.started", "1.0.0", TaskPlanningStartedPayload)
register("task.planning.started", "1.0.1", TaskPlanningStartedPayload)
register("task.plan.ready", "1.0.0", TaskPlanReadyPayload)
register("task.plan.ready", "1.0.1", TaskPlanReadyPayload)
register("task.execution.started", "1.0.0", TaskExecutionStartedPayload)
register("task.execution.started", "1.0.1", TaskExecutionStartedPayload)

# Story 2.8 — 4 new event types.
register("task.blocker_raised", "1.0.0", TaskBlockerRaisedPayload)
register("task.blocker_raised", "1.0.1", TaskBlockerRaisedPayload)
register("task.summary_emitted", "1.0.0", TaskSummaryEmittedPayload)
register("task.summary_emitted", "1.0.1", TaskSummaryEmittedPayload)
register("task.approval_requested", "1.0.0", TaskApprovalRequestedPayload)
register("task.approval_requested", "1.0.1", TaskApprovalRequestedPayload)
# Story 3.10 AC-1 / AC-11: additive 1.1.0 with optional FR14 fields. Same-model
# contract — pre-3.10 events (no risk_class / pre_check_results / diff_summary /
# accepted_commands) deserialize cleanly under 1.1.0 with the new fields
# defaulting to None (NFR-M3 additive-only). Story 3.9 H7 carry-forward —
# registration in event_types.py (NOT packages/events/.../schema_registry.py)
# avoids the circular import the dev pass discovered.
register("task.approval_requested", "1.1.0", TaskApprovalRequestedPayload)
register("task.completed", "1.0.0", TaskCompletedPayload)
register("task.completed", "1.0.1", TaskCompletedPayload)

# Story 2.10 — 4 failure-detection event types (FR24a, NFR-R5).
register("service.crashed", "1.0.0", ServiceCrashedPayload)
register("service.crashed", "1.0.1", ServiceCrashedPayload)
register("session.heartbeat_timeout", "1.0.0", SessionHeartbeatTimeoutPayload)
register("session.heartbeat_timeout", "1.0.1", SessionHeartbeatTimeoutPayload)
register("sink.delivery_failed", "1.0.0", SinkDeliveryFailedPayload)
register("sink.delivery_failed", "1.0.1", SinkDeliveryFailedPayload)
register("task.stop_requested", "1.0.0", TaskStopRequestedPayload)
register("task.stop_requested", "1.0.1", TaskStopRequestedPayload)

# Story 2.14 — register all 12 task-event payload models under v1.0.1 alongside
# their v1.0.0 entries. The migrator (`scripts/migrator/`) emits v1.0.1
# envelopes carrying the additive ``extensions`` envelope-level field; the
# payload models are unchanged. Per the schema_registry's idempotent
# same-model contract, re-registering the SAME model under both versions
# is permitted (NFR-M3 additive-only evolution within a major version).
# After Story 2.14, ``EVENT_TYPES`` is unchanged at 12 distinct type names;
# ``REGISTRY`` doubles to 24 (type, version) entries.

# Story 2.16 — register the secret.accessed audit-event payload (FR42 /
# NFR-S3). One new bare type name (``EVENT_TYPES`` grows 12 → 13); two new
# (type, version) entries. Same-model contract: identical payload model
# registered under both v1.0.0 and v1.0.1.
register("secret.accessed", "1.0.0", SecretAccessedPayload)
register("secret.accessed", "1.0.1", SecretAccessedPayload)

# Story 3.2 — register the telegram.rejected event payload (FR11 / NFR-S4).
# One new bare type name (``EVENT_TYPES`` grows 13 → 14); two new
# (type, version) entries. Same-model contract: identical payload model
# registered under both v1.0.0 and v1.0.1 (Story 2.14 additive-version rule).
register("telegram.rejected", "1.0.0", TelegramRejectedPayload)
register("telegram.rejected", "1.0.1", TelegramRejectedPayload)

__all__ = [
    "TELEGRAM_REJECTED_SCHEMA_VERSION",
    "AcceptedCommand",
    "DiffSummary",
    "PreCheckOutcome",
    "PreCheckResults",
    "SecretAccessedPayload",
    "ServiceCrashedPayload",
    "SessionHeartbeatTimeoutPayload",
    "SinkDeliveryFailedPayload",
    "TaskApprovalRequestedPayload",
    "TaskBlockerRaisedPayload",
    "TaskCompletedPayload",
    "TaskCreatedPayload",
    "TaskExecutionStartedPayload",
    "TaskPlanReadyPayload",
    "TaskPlanningStartedPayload",
    "TaskStopRequestedPayload",
    "TaskSummaryEmittedPayload",
    "TelegramRejectedPayload",
]
