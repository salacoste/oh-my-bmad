"""Schema-registry registrations for task event types.

Story 3.5.2 relocated the Pydantic payload model definitions to
``packages/events/payloads.py`` so that all services can import them without
cross-service import violations (``# noqa: IMP001``). This file re-exports
all symbols from the new location for backward compatibility and retains the
``register()`` calls here because moving them into ``packages/events/``
triggers a circular import:

    events.__init__ → registry_state.__init__ →
    registry_state.adapters.event_log → events.EventEnvelope

while the events package is still initializing.

The models are in ``events.payloads``; the registration side-effects stay
service-side.
"""

from __future__ import annotations

# Re-export all payload models and supporting types from the shared package.
from events.payloads import (  # noqa: F401 — intentional re-exports
    TELEGRAM_REJECTED_SCHEMA_VERSION,
    AcceptedCommand,
    AgentReasoningBreadcrumbPayload,
    DiffSummary,
    FileEditedPayload,
    PreCheckOutcome,
    PreCheckResults,
    SecretAccessedPayload,
    ServiceCrashedPayload,
    SessionFinishedPayload,
    SessionHeartbeatPayload,
    SessionHeartbeatTimeoutPayload,
    SessionStartedPayload,
    SinkDeliveryFailedPayload,
    TaskApprovalRequestedPayload,
    TaskBlockerRaisedPayload,
    TaskCompletedPayload,
    TaskCreatedPayload,
    TaskExecutionStartedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
    TaskSelfRecoveredPayload,
    TaskStopRequestedPayload,
    TaskSummaryEmittedPayload,
    TelegramRejectedPayload,
)
from events.schema_registry import register

__all__ = [
    "TELEGRAM_REJECTED_SCHEMA_VERSION",
    "AcceptedCommand",
    "AgentReasoningBreadcrumbPayload",
    "DiffSummary",
    "FileEditedPayload",
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
    "TaskCompletedPayload",
    "TaskCreatedPayload",
    "TaskExecutionStartedPayload",
    "TaskPlanReadyPayload",
    "TaskPlanningStartedPayload",
    "TaskSelfRecoveredPayload",
    "TaskStopRequestedPayload",
    "TaskSummaryEmittedPayload",
    "TelegramRejectedPayload",
]

# ---------------------------------------------------------------------------
# Register all event types with Story 2.1's schema_registry.
# Idempotent: re-registering the same model for the same key is a no-op.
# ---------------------------------------------------------------------------

register("task.created", "1.0.0", TaskCreatedPayload)
register("task.created", "1.0.1", TaskCreatedPayload)
register("task.created", "1.1.0", TaskCreatedPayload)
register("task.planning.started", "1.0.0", TaskPlanningStartedPayload)
register("task.planning.started", "1.0.1", TaskPlanningStartedPayload)
register("task.plan.ready", "1.0.0", TaskPlanReadyPayload)
register("task.plan.ready", "1.0.1", TaskPlanReadyPayload)
register("task.plan.ready", "1.1.0", TaskPlanReadyPayload)  # Story 5.11 — structured plan steps
register("task.execution.started", "1.0.0", TaskExecutionStartedPayload)
register("task.execution.started", "1.0.1", TaskExecutionStartedPayload)

# Story 2.8 — 4 new event types.
register("task.blocker_raised", "1.0.0", TaskBlockerRaisedPayload)
register("task.blocker_raised", "1.0.1", TaskBlockerRaisedPayload)
register("task.blocker_raised", "1.1.0", TaskBlockerRaisedPayload)
register("task.summary_emitted", "1.0.0", TaskSummaryEmittedPayload)
register("task.summary_emitted", "1.0.1", TaskSummaryEmittedPayload)
register("task.approval_requested", "1.0.0", TaskApprovalRequestedPayload)
register("task.approval_requested", "1.0.1", TaskApprovalRequestedPayload)
register("task.approval_requested", "1.1.0", TaskApprovalRequestedPayload)
register("task.completed", "1.0.0", TaskCompletedPayload)
register("task.completed", "1.0.1", TaskCompletedPayload)
register("task.completed", "1.1.0", TaskCompletedPayload)

# Story 2.10 — 4 failure-detection event types (FR24a, NFR-R5).
register("service.crashed", "1.0.0", ServiceCrashedPayload)
register("service.crashed", "1.0.1", ServiceCrashedPayload)
register("session.heartbeat_timeout", "1.0.0", SessionHeartbeatTimeoutPayload)
register("session.heartbeat_timeout", "1.0.1", SessionHeartbeatTimeoutPayload)
register("sink.delivery_failed", "1.0.0", SinkDeliveryFailedPayload)
register("sink.delivery_failed", "1.0.1", SinkDeliveryFailedPayload)
register("task.stop_requested", "1.0.0", TaskStopRequestedPayload)
register("task.stop_requested", "1.0.1", TaskStopRequestedPayload)

# Story 2.14 — v1.0.1 registrations (same models, additive envelope field).
# Story 2.16 — secret.accessed audit-event payload (FR42 / NFR-S3).
register("secret.accessed", "1.0.0", SecretAccessedPayload)
register("secret.accessed", "1.0.1", SecretAccessedPayload)

# Story 3.2 — telegram.rejected event payload (FR11 / NFR-S4).
register("telegram.rejected", "1.0.0", TelegramRejectedPayload)
register("telegram.rejected", "1.0.1", TelegramRejectedPayload)

# Story 3.13 — task.self_recovered event payload (FR16).
register("task.self_recovered", "1.0.0", TaskSelfRecoveredPayload)

# Story 5.2 — session lifecycle event payloads (FR24a).
register("session.started", "1.0.0", SessionStartedPayload)
register("session.started", "1.0.1", SessionStartedPayload)
register("session.heartbeat", "1.0.0", SessionHeartbeatPayload)
register("session.heartbeat", "1.0.1", SessionHeartbeatPayload)
register("session.finished", "1.0.0", SessionFinishedPayload)
register("session.finished", "1.0.1", SessionFinishedPayload)

# Story 5.5 — agent.reasoning.* breadcrumb payloads (FR17b, NFR-O6).
register("agent.reasoning.plan_drafted", "1.0.0", AgentReasoningBreadcrumbPayload)
register("agent.reasoning.tool_call_rationale", "1.0.0", AgentReasoningBreadcrumbPayload)
register("agent.reasoning.step_summary", "1.0.0", AgentReasoningBreadcrumbPayload)

# Story 5.6 — file.edited event payload (FR30, NFR-R2).
register("file.edited", "1.0.0", FileEditedPayload)
register("file.edited", "1.0.1", FileEditedPayload)
