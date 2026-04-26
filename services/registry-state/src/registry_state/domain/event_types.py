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
"""

from __future__ import annotations

from datetime import datetime

from events.schema_registry import register
from pydantic import BaseModel, ConfigDict, Field


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
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    service: str
    exit_code: int


class SessionHeartbeatTimeoutPayload(BaseModel):
    """Payload for the ``session.heartbeat_timeout`` event.

    Emitted when a session's last heartbeat is older than 2× the configured
    heartbeat interval (strict ``>`` boundary — see :class:`HeartbeatMonitor`).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    session_id: str
    task_id: str
    last_heartbeat_at: datetime
    timeout_threshold_s: float


class SinkDeliveryFailedPayload(BaseModel):
    """Payload for the ``sink.delivery_failed`` event.

    Emitted when a sink (e.g. Telegram) has accumulated ``failure_threshold``
    consecutive delivery failures. ``last_error`` MUST be sanitized by the
    caller — no secrets, tokens, or PII.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    sink_name: str
    consecutive_failures: int
    last_error: str | None = None


class TaskStopRequestedPayload(BaseModel):
    """Payload for the ``task.stop_requested`` event.

    Emitted when an operator (Telegram, console, etc.) requests that an
    in-flight task stop. Materializer state transition (e.g. ``tasks.status =
    "stopped"``) is wired in Epic 3.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str
    actor_id: str


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
