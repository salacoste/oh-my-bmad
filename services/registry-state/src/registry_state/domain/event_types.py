"""Payload models + schema-registry registrations for the 4 task event types (Story 2.5).

These are the FIRST concrete event types registered in the platform.
Story 2.1 shipped an empty REGISTRY; Story 2.5 begins populating it.

All models use ``ConfigDict(frozen=True, strict=True, extra="forbid")``
matching the Story 2.1 discipline. Registration calls are at module bottom
so the side-effect runs once on import (idempotent: same model for same key
is a no-op per Story 2.1's schema_registry.register contract).
"""

from __future__ import annotations

from events.schema_registry import register
from pydantic import BaseModel, ConfigDict


class TaskCreatedPayload(BaseModel):
    """Payload for the ``task.created`` event."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    task_id: str
    title: str | None = None


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

register("task.created", "1.0.0", TaskCreatedPayload)
register("task.planning.started", "1.0.0", TaskPlanningStartedPayload)
register("task.plan.ready", "1.0.0", TaskPlanReadyPayload)
register("task.execution.started", "1.0.0", TaskExecutionStartedPayload)

__all__ = [
    "TaskCreatedPayload",
    "TaskExecutionStartedPayload",
    "TaskPlanReadyPayload",
    "TaskPlanningStartedPayload",
]
