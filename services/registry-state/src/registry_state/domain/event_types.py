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
from typing import TYPE_CHECKING

from events.schema_registry import register
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    pass


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
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str
    title: str | None = Field(default=None, max_length=512)
    repo: str | None = Field(default=None, max_length=2048)
    hint: str | None = Field(default=None, max_length=4096)


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


class TaskApprovalRequestedPayload(BaseModel):
    """Payload for the ``task.approval_requested`` event."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str
    action: str
    justification: str


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


# ---------------------------------------------------------------------------
# Register all event types with Story 2.1's schema_registry.
# Idempotent: re-registering the same model for the same key is a no-op.
# ---------------------------------------------------------------------------

register("task.created", "1.0.0", TaskCreatedPayload)
register("task.planning.started", "1.0.0", TaskPlanningStartedPayload)
register("task.plan.ready", "1.0.0", TaskPlanReadyPayload)
register("task.execution.started", "1.0.0", TaskExecutionStartedPayload)

# Story 2.8 — 4 new event types.
register("task.blocker_raised", "1.0.0", TaskBlockerRaisedPayload)
register("task.summary_emitted", "1.0.0", TaskSummaryEmittedPayload)
register("task.approval_requested", "1.0.0", TaskApprovalRequestedPayload)
register("task.completed", "1.0.0", TaskCompletedPayload)

# Story 2.10 — 4 failure-detection event types (FR24a, NFR-R5).
register("service.crashed", "1.0.0", ServiceCrashedPayload)
register("session.heartbeat_timeout", "1.0.0", SessionHeartbeatTimeoutPayload)
register("sink.delivery_failed", "1.0.0", SinkDeliveryFailedPayload)
register("task.stop_requested", "1.0.0", TaskStopRequestedPayload)

__all__ = [
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
]
