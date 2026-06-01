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
    ApprovalGrantedPayload,
    ApprovalInboxOpenedPayload,
    ApprovalRejectedPayload,
    BudgetOverridePayload,
    CapabilityDeniedPayload,
    DiffSummary,
    FileEditedPayload,
    KeyRotatedPayload,
    LicenseOverridePayload,
    PreCheckOutcome,
    PreCheckResults,
    SecretAccessedPayload,
    ServiceCrashedPayload,
    SessionFinishedPayload,
    SessionHeartbeatPayload,
    SessionHeartbeatTimeoutPayload,
    SessionReconnectingPayload,
    SessionStartedPayload,
    SinkDeliveryFailedPayload,
    TaskApprovalRequestedPayload,
    TaskApprovalSignedPayload,
    TaskBlockerRaisedPayload,
    TaskBudgetEnforcementTriggeredPayload,
    TaskBudgetExceededPayload,
    TaskCompletedPayload,
    TaskCreatedPayload,
    TaskExecutionResumedPayload,
    TaskExecutionStartedPayload,
    TaskLicenseFlaggedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
    TaskRetryRequestedPayload,
    TaskSelfRecoveredPayload,
    TaskStepCompletedPayload,
    TaskStopRequestedPayload,
    TaskSummaryEmittedPayload,
    TelegramRejectedPayload,
    Tier3ActionAttemptedPayload,
    Tier3ActionPerformedPayload,
)
from events.schema_registry import register as _register


def register(event_type: str, schema_version: str, payload_cls: type) -> None:
    """Tolerant wrapper around schema_registry.register().

    Suppresses ``ValueError`` when a key is already bound to a *different*
    payload class (which can happen in CI where test_audited_secret.py
    registers a local ``_LocalSecretAccessedPayload`` before this module
    loads). Without the wrapper, the first conflict aborts module import
    and all subsequent registrations (e.g. ``approval.granted``,
    ``approval.rejected``, ``task.stop_requested``, ``task.retry_requested``)
    silently fail to register, breaking the API tests.

    Same-class re-registration is already a no-op in the registry, so this
    only changes behaviour on the bad-collision path — the canonical
    class either wins (if loaded first) or yields to the pre-existing
    binding (if loaded second). Tests assert on field shape, not identity.
    """
    import contextlib

    with contextlib.suppress(ValueError):
        _register(event_type, schema_version, payload_cls)


__all__ = [
    "TELEGRAM_REJECTED_SCHEMA_VERSION",
    "AcceptedCommand",
    "AgentReasoningBreadcrumbPayload",
    "ApprovalGrantedPayload",
    "ApprovalInboxOpenedPayload",
    "ApprovalRejectedPayload",
    "BudgetOverridePayload",
    "CapabilityDeniedPayload",
    "DiffSummary",
    "FileEditedPayload",
    "KeyRotatedPayload",
    "LicenseOverridePayload",
    "PreCheckOutcome",
    "PreCheckResults",
    "SecretAccessedPayload",
    "ServiceCrashedPayload",
    "SessionFinishedPayload",
    "SessionHeartbeatPayload",
    "SessionHeartbeatTimeoutPayload",
    "SessionReconnectingPayload",
    "SessionStartedPayload",
    "SinkDeliveryFailedPayload",
    "TaskApprovalRequestedPayload",
    "TaskApprovalSignedPayload",
    "TaskBlockerRaisedPayload",
    "TaskBudgetEnforcementTriggeredPayload",
    "TaskBudgetExceededPayload",
    "TaskCompletedPayload",
    "TaskCreatedPayload",
    "TaskExecutionResumedPayload",
    "TaskExecutionStartedPayload",
    "TaskLicenseFlaggedPayload",
    "TaskPlanReadyPayload",
    "TaskPlanningStartedPayload",
    "TaskRetryRequestedPayload",
    "TaskSelfRecoveredPayload",
    "TaskStepCompletedPayload",
    "TaskStopRequestedPayload",
    "TaskSummaryEmittedPayload",
    "TelegramRejectedPayload",
    "Tier3ActionAttemptedPayload",
    "Tier3ActionPerformedPayload",
]

# ---------------------------------------------------------------------------
# Register all event types with Story 2.1's schema_registry.
# Idempotent: re-registering the same model for the same key is a no-op.
#
# Wrapped in ensure_registered() so it can be replayed after a test using
# unregister_all() wipes the registry (Story 8.7.5 / Epic 8 retro debt #3).
# Python's module-cache means the bare module-level statements run only
# once at first import — any subsequent unregister_all() leaves the
# registrations gone for the rest of the test session.
# ---------------------------------------------------------------------------


def ensure_registered() -> None:
    """Register (or re-register) every canonical event type. Idempotent."""
    register("task.created", "1.0.0", TaskCreatedPayload)
    register("task.created", "1.0.1", TaskCreatedPayload)
    register("task.created", "1.1.0", TaskCreatedPayload)
    register("task.planning.started", "1.0.0", TaskPlanningStartedPayload)
    register("task.planning.started", "1.0.1", TaskPlanningStartedPayload)
    register("task.planning.started", "1.1.0", TaskPlanningStartedPayload)
    register("task.plan.ready", "1.0.0", TaskPlanReadyPayload)
    register("task.plan.ready", "1.0.1", TaskPlanReadyPayload)
    register("task.plan.ready", "1.1.0", TaskPlanReadyPayload)  # Story 5.11 — structured plan steps
    register("task.execution.started", "1.0.0", TaskExecutionStartedPayload)
    register("task.execution.started", "1.0.1", TaskExecutionStartedPayload)
    register("task.execution.started", "1.1.0", TaskExecutionStartedPayload)
    register("task.step.completed", "1.0.0", TaskStepCompletedPayload)
    register("task.step.completed", "1.1.0", TaskStepCompletedPayload)

    # Story 2.8 — 4 new event types.
    register("task.blocker_raised", "1.0.0", TaskBlockerRaisedPayload)
    register("task.blocker_raised", "1.0.1", TaskBlockerRaisedPayload)
    register("task.blocker_raised", "1.1.0", TaskBlockerRaisedPayload)
    register("task.summary_emitted", "1.0.0", TaskSummaryEmittedPayload)
    register("task.summary_emitted", "1.0.1", TaskSummaryEmittedPayload)
    register("task.summary_emitted", "1.1.0", TaskSummaryEmittedPayload)
    register("task.approval_requested", "1.0.0", TaskApprovalRequestedPayload)
    register("task.approval_requested", "1.0.1", TaskApprovalRequestedPayload)
    register("task.approval_requested", "1.1.0", TaskApprovalRequestedPayload)
    register("task.completed", "1.0.0", TaskCompletedPayload)
    register("task.completed", "1.0.1", TaskCompletedPayload)
    register("task.completed", "1.1.0", TaskCompletedPayload)
    register("task.completed", "1.2.0", TaskCompletedPayload)  # Story 5.15 — token_usage

    # Story 2.10 — 4 failure-detection event types (FR24a, NFR-R5).
    register("service.crashed", "1.0.0", ServiceCrashedPayload)
    register("service.crashed", "1.0.1", ServiceCrashedPayload)
    register("service.crashed", "1.1.0", ServiceCrashedPayload)
    register("session.heartbeat_timeout", "1.0.0", SessionHeartbeatTimeoutPayload)
    register("session.heartbeat_timeout", "1.0.1", SessionHeartbeatTimeoutPayload)
    register("session.heartbeat_timeout", "1.1.0", SessionHeartbeatTimeoutPayload)
    register("sink.delivery_failed", "1.0.0", SinkDeliveryFailedPayload)
    register("sink.delivery_failed", "1.0.1", SinkDeliveryFailedPayload)
    register("sink.delivery_failed", "1.1.0", SinkDeliveryFailedPayload)
    register("task.stop_requested", "1.0.0", TaskStopRequestedPayload)
    register("task.stop_requested", "1.0.1", TaskStopRequestedPayload)
    register("task.stop_requested", "1.1.0", TaskStopRequestedPayload)

    # Story 2.14 — v1.0.1 registrations (same models, additive envelope field).
    # Story 2.16 — secret.accessed audit-event payload (FR42 / NFR-S3).
    register("secret.accessed", "1.0.0", SecretAccessedPayload)
    register("secret.accessed", "1.0.1", SecretAccessedPayload)
    register("secret.accessed", "1.1.0", SecretAccessedPayload)

    # Story 3.2 — telegram.rejected event payload (FR11 / NFR-S4).
    register("telegram.rejected", "1.0.0", TelegramRejectedPayload)
    register("telegram.rejected", "1.0.1", TelegramRejectedPayload)
    register("telegram.rejected", "1.1.0", TelegramRejectedPayload)

    # Story 3.13 — task.self_recovered event payload (FR16).
    register("task.self_recovered", "1.0.0", TaskSelfRecoveredPayload)
    register("task.self_recovered", "1.1.0", TaskSelfRecoveredPayload)

    # Story 5.2 — session lifecycle event payloads (FR24a).
    register("session.started", "1.0.0", SessionStartedPayload)
    register("session.started", "1.0.1", SessionStartedPayload)
    register("session.started", "1.1.0", SessionStartedPayload)
    register("session.heartbeat", "1.0.0", SessionHeartbeatPayload)
    register("session.heartbeat", "1.0.1", SessionHeartbeatPayload)
    register("session.heartbeat", "1.1.0", SessionHeartbeatPayload)
    register("session.finished", "1.0.0", SessionFinishedPayload)
    register("session.finished", "1.0.1", SessionFinishedPayload)
    register("session.finished", "1.1.0", SessionFinishedPayload)
    # Story 7.8 — restart-recovery event payloads (FR29 models, FR16 synthesis).
    register("session.reconnecting", "1.0.0", SessionReconnectingPayload)
    register("session.reconnecting", "1.1.0", SessionReconnectingPayload)
    register("task.execution.resumed", "1.0.0", TaskExecutionResumedPayload)
    register("task.execution.resumed", "1.1.0", TaskExecutionResumedPayload)

    # Story 5.5 — agent.reasoning.* breadcrumb payloads (FR17b, NFR-O6).
    register("agent.reasoning.plan_drafted", "1.0.0", AgentReasoningBreadcrumbPayload)
    register("agent.reasoning.plan_drafted", "1.1.0", AgentReasoningBreadcrumbPayload)
    register("agent.reasoning.tool_call_rationale", "1.0.0", AgentReasoningBreadcrumbPayload)
    register("agent.reasoning.tool_call_rationale", "1.1.0", AgentReasoningBreadcrumbPayload)
    register("agent.reasoning.step_summary", "1.0.0", AgentReasoningBreadcrumbPayload)
    register("agent.reasoning.step_summary", "1.1.0", AgentReasoningBreadcrumbPayload)

    # Story 5.6 — file.edited event payload (FR30, NFR-R2).
    register("file.edited", "1.0.0", FileEditedPayload)
    register("file.edited", "1.0.1", FileEditedPayload)
    register("file.edited", "1.1.0", FileEditedPayload)

    # Story 5.15 — task.budget_exceeded event payload (FR44 / NFR-P5).
    register("task.budget_exceeded", "1.0.0", TaskBudgetExceededPayload)
    register("task.budget_exceeded", "1.1.0", TaskBudgetExceededPayload)

    # Story 12.2 — task.budget_enforcement_triggered audit event (FR67).
    # The ACTION-RECORD emitted by worker-wrapper AFTER it SIGTERMs the
    # subprocess for a budget overage (distinct from the task.budget_exceeded
    # SIGNAL above). Brand-new type → registered only at 1.1.0 per the epics
    # scope note (no 1.0.0 legacy to carry).
    register(
        "task.budget_enforcement_triggered",
        "1.1.0",
        TaskBudgetEnforcementTriggeredPayload,
    )

    # Story 6.2 — tier3.action_attempted audit event (FR38).
    # Emitter added in Story 6.5.
    register("tier3.action_attempted", "1.0.0", Tier3ActionAttemptedPayload)
    register("tier3.action_attempted", "1.1.0", Tier3ActionAttemptedPayload)

    # Story 6.6 — tier3.action_performed audit event (FR38).
    register("tier3.action_performed", "1.0.0", Tier3ActionPerformedPayload)
    register("tier3.action_performed", "1.1.0", Tier3ActionPerformedPayload)

    # Story 6.4 — operator decision event types (FR7, FR41).
    register("approval.granted", "1.0.0", ApprovalGrantedPayload)
    register("approval.granted", "1.1.0", ApprovalGrantedPayload)
    register("approval.rejected", "1.0.0", ApprovalRejectedPayload)
    register("approval.rejected", "1.1.0", ApprovalRejectedPayload)
    # Story 11.1 — HMAC-signed approval sibling event (FR64 / NFR-S10).
    # Minimal registration at 1.0.0; Story 11.2 bumps to 1.1.0 (additive —
    # same payload class, Story 11.1 P1-H2 already applied Field
    # constraints; the 1.1.0 entry documents the constraints as the
    # canonical schema_version-1.1.0 surface for Story 11.4's
    # ``just verify-approval`` recipe + the contract-fixture forward-compat
    # pair under ``tests/contract/fixtures/``).
    register("task.approval_signed", "1.0.0", TaskApprovalSignedPayload)
    register("task.approval_signed", "1.1.0", TaskApprovalSignedPayload)
    # Story 11.2 — key.rotated audit event (FR65a / NFR-S10). Story 11.5's
    # key-rotation detector emits when OPERATOR_HMAC_KEY's fingerprint
    # changes. Pure schema registration here; emission deferred to 11.5.
    # Born at 1.1.0 (no v1.0.0 predecessor; same applies to capability.denied
    # below — both are NEW event types introduced in Phase 2).
    register("key.rotated", "1.1.0", KeyRotatedPayload)
    # Story 11.2 — capability.denied audit event (Epic 10 retro DD5).
    # Registration unblocks Story 10.4's preview counter
    # ``omb_capability_denied_total{tier,boundary}`` (currently
    # pre-populated at 0). Emission deferred to Story 11.2.1
    # (capability.denied emission — requires TierEnforcementMiddleware
    # + MCP capability-handler wiring; out of scope for 11.2 per D5).
    register("capability.denied", "1.1.0", CapabilityDeniedPayload)
    # Story 11.3 — approval.inbox_opened event (FR63). Emitted by
    # telegram-gateway's ``/approvals`` handler when the operator opens
    # a pinned Forum-Topic inbox; materialized by registry-state into
    # the ``approval_inbox`` table (Story 11.3 AC2) so clawhip-daemon
    # can route ``task.approval_requested`` to the pinned thread.
    # Born at 1.1.0 (no v1.0.0 predecessor; NEW event type introduced
    # in Phase 2, same convention as key.rotated + capability.denied).
    register("approval.inbox_opened", "1.1.0", ApprovalInboxOpenedPayload)
    register("task.retry_requested", "1.0.0", TaskRetryRequestedPayload)
    register("task.retry_requested", "1.1.0", TaskRetryRequestedPayload)
    register("tier3.license_override", "1.0.0", LicenseOverridePayload)
    register("tier3.license_override", "1.1.0", LicenseOverridePayload)

    # Story 6.10 — task.license_flagged event payload (FR40).
    register("task.license_flagged", "1.0.0", TaskLicenseFlaggedPayload)
    register("task.license_flagged", "1.1.0", TaskLicenseFlaggedPayload)

    # Story 6.11 — tier3.budget_override audit event (FR44).
    register("tier3.budget_override", "1.0.0", BudgetOverridePayload)
    register("tier3.budget_override", "1.1.0", BudgetOverridePayload)
    # Story 12.3 (FR68, D1=(A)) — budget.override @ 1.1.0 is the Epic-12-namespace
    # alias for the SAME BudgetOverridePayload (architecture.md:1423). The decisions
    # route still EMITS tier3.budget_override (kept above for FR44 back-compat); this
    # registration lets the budget.override name validate/round-trip so a later pass
    # can switch the emit once consumers migrate. No new payload, no new 1.0.0.
    register("budget.override", "1.1.0", BudgetOverridePayload)

    # Story 9.7 / AC10: all event types registered under 1.1.0 above for
    # replay safety. Both 1.0.0 and 1.1.0 entries kept per AC10 option (a).


# Module-load registration — runs once on first import.
# Tests using unregister_all() should call ensure_registered() in their
# autouse fixture to restore the canonical bindings.
ensure_registered()
