"""Payload models + schema-registry registrations for task event types.

Story 2.5 ships the first 4 event types. Story 2.8 extends with 4 more:
  - task.blocker_raised
  - task.summary_emitted
  - task.approval_requested
  - task.completed

All models use ``ConfigDict(frozen=True, strict=True, extra="forbid")``
matching the Story 2.1 discipline. Registration calls are at module bottom
so the side-effect runs once on import (idempotent: same model for same key
is a no-op per Story 2.1's schema_registry.register contract).
"""

from __future__ import annotations

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
# Register all 4 event types with Story 2.1's schema_registry.
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

__all__ = [
    "TaskApprovalRequestedPayload",
    "TaskBlockerRaisedPayload",
    "TaskCompletedPayload",
    "TaskCreatedPayload",
    "TaskExecutionStartedPayload",
    "TaskPlanReadyPayload",
    "TaskPlanningStartedPayload",
    "TaskSummaryEmittedPayload",
]
