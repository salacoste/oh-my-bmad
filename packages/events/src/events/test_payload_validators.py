"""Tests for cross-renderer uniform validators (Story 7.5.8).

Covers:
- task_id pattern validation on all Task*Payload models
- pr_branch git-ref-name pattern validation
"""

from __future__ import annotations

from datetime import UTC

import pytest

from events.payloads import (
    PlanStep,
    TaskApprovalRequestedPayload,
    TaskBlockerRaisedPayload,
    TaskBudgetEnforcementTriggeredPayload,
    TaskBudgetExceededPayload,
    TaskCompletedPayload,
    TaskCreatedPayload,
    TaskExecutionStartedPayload,
    TaskLicenseFlaggedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
    TaskRetryRequestedPayload,
    TaskSelfRecoveredPayload,
    TaskStepCompletedPayload,
    TaskStopRequestedPayload,
    TaskSummaryEmittedPayload,
)

# ---------------------------------------------------------------------------
# task_id pattern — valid inputs
# ---------------------------------------------------------------------------

_VALID_TASK_IDS = [
    "t-01234567-89ab-7def-8abc-0123456789ab",
    "t-aaaaaaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee",
    "t-00000000-0000-7000-8000-000000000000",
    "t-fedd0174-5e7e-7f3a-b78c-3e23dfb0c920",
]


@pytest.mark.parametrize("task_id", _VALID_TASK_IDS)
class TestTaskIdPatternValid:
    """Every payload model should accept valid task_id formats."""

    def test_created(self, task_id: str) -> None:
        TaskCreatedPayload(task_id=task_id)

    def test_planning_started(self, task_id: str) -> None:
        TaskPlanningStartedPayload(task_id=task_id)

    def test_plan_ready(self, task_id: str) -> None:
        TaskPlanReadyPayload(task_id=task_id, plan_summary="test")

    def test_execution_started(self, task_id: str) -> None:
        TaskExecutionStartedPayload(
            task_id=task_id,
            session_id="s-01234567-89ab-7def-8abc-0123456789ab",
        )

    def test_step_completed(self, task_id: str) -> None:
        TaskStepCompletedPayload(task_id=task_id, step=1, description="done", output_summary="ok")

    def test_blocker_raised(self, task_id: str) -> None:
        TaskBlockerRaisedPayload(task_id=task_id, reason="stuck")

    def test_summary_emitted(self, task_id: str) -> None:
        TaskSummaryEmittedPayload(task_id=task_id, summary="done")

    def test_completed(self, task_id: str) -> None:
        TaskCompletedPayload(task_id=task_id, summary="done")

    def test_stop_requested(self, task_id: str) -> None:
        TaskStopRequestedPayload(task_id=task_id, actor_id="console")

    def test_self_recovered(self, task_id: str) -> None:
        from datetime import datetime

        TaskSelfRecoveredPayload(
            task_id=task_id,
            recovered_at=datetime(2026, 1, 1, tzinfo=UTC),
            events_replayed=3,
            replay_duration_ms=100,
        )

    def test_budget_exceeded(self, task_id: str) -> None:
        TaskBudgetExceededPayload(task_id=task_id, token_limit=1000, tokens_used=1500, step=5)

    def test_budget_enforcement_triggered(self, task_id: str) -> None:
        # Story 12.2 / FR67 — happy path: all FR67 fields validate.
        p = TaskBudgetEnforcementTriggeredPayload(
            task_id=task_id,
            budget_threshold=100_000,
            actual_spend=105_000,
            action_taken="subprocess_terminated",
            post_trigger_transition="failed",
            step=3,
        )
        assert p.action_taken == "subprocess_terminated"
        assert p.post_trigger_transition == "failed"

    def test_budget_enforcement_triggered_override_intercepted(self, task_id: str) -> None:
        # Story 12.3a Phase 2 — additive enum value: the override landed in the
        # grace window, the subprocess was NOT terminated, and the task
        # continues under the extended budget (audit-truthful pairing of
        # action_taken="override_intercepted" with
        # post_trigger_transition="awaiting_approval").
        p = TaskBudgetEnforcementTriggeredPayload(
            task_id=task_id,
            budget_threshold=1_000,
            actual_spend=1_500,
            action_taken="override_intercepted",
            post_trigger_transition="awaiting_approval",
            step=2,
        )
        assert p.action_taken == "override_intercepted"
        assert p.post_trigger_transition == "awaiting_approval"

    def test_budget_enforcement_triggered_rejects_bad_input(self, task_id: str) -> None:
        # Story 12.2 / FR67 — frozen+strict+extra=forbid; constrained literals.
        import pytest
        from pydantic import ValidationError

        # extra field forbidden
        with pytest.raises(ValidationError):
            TaskBudgetEnforcementTriggeredPayload(
                task_id=task_id,
                budget_threshold=1,
                actual_spend=2,
                post_trigger_transition="failed",
                step=1,
                bogus="x",  # type: ignore[call-arg]
            )
        # action_taken constrained to the single literal
        with pytest.raises(ValidationError):
            TaskBudgetEnforcementTriggeredPayload(
                task_id=task_id,
                budget_threshold=1,
                actual_spend=2,
                action_taken="something_else",  # type: ignore[arg-type]
                post_trigger_transition="failed",
                step=1,
            )
        # post_trigger_transition constrained to failed|awaiting_approval
        with pytest.raises(ValidationError):
            TaskBudgetEnforcementTriggeredPayload(
                task_id=task_id,
                budget_threshold=1,
                actual_spend=2,
                post_trigger_transition="cancelled",  # type: ignore[arg-type]
                step=1,
            )
        # spend/threshold must be > 0
        with pytest.raises(ValidationError):
            TaskBudgetEnforcementTriggeredPayload(
                task_id=task_id,
                budget_threshold=0,
                actual_spend=2,
                post_trigger_transition="failed",
                step=1,
            )

    def test_approval_requested(self, task_id: str) -> None:
        TaskApprovalRequestedPayload(task_id=task_id, action="approve", justification="looks good")

    def test_retry_requested(self, task_id: str) -> None:
        TaskRetryRequestedPayload(task_id=task_id, decision_id="d-0", actor_id="console")

    def test_license_flagged(self, task_id: str) -> None:
        TaskLicenseFlaggedPayload(
            task_id=task_id, reason_code="GPL", file_list=["a.py"], detected_licenses=["GPL-3.0"]
        )


# ---------------------------------------------------------------------------
# task_id pattern — invalid inputs
# ---------------------------------------------------------------------------

_INVALID_TASK_IDS = [
    "task-abc",  # wrong prefix
    "T-01234567-89ab-7def-8abc-0123456789ab",  # uppercase T
    "t-01234567-89ab-6def-8abc-0123456789ab",  # version 6, not 7
    "t-01234567-89ab-7def-cabc-0123456789ab",  # variant c not in [89ab]
    "t-<x>",  # special chars
    "",  # empty
    "t-",  # missing UUID
    "01234567-89ab-7def-8abc-0123456789ab",  # missing prefix
]


@pytest.mark.parametrize("task_id", _INVALID_TASK_IDS)
def test_task_id_rejects_invalid(task_id: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        TaskCompletedPayload(task_id=task_id, summary="test")


# ---------------------------------------------------------------------------
# pr_branch pattern — valid inputs
# ---------------------------------------------------------------------------

_VALID_PR_BRANCHES = [
    "main",
    "feat/my-feature",
    "fix/bug-123",
    "release/v2.0",
    "feature_branch",
    "v1.2.3",
    "a",
    "ABC123",
    "my-feature-v2",
    "feat/sub/branch",
    "release_1",
    "v2.0.1-rc1",
]


@pytest.mark.parametrize("branch", _VALID_PR_BRANCHES)
def test_pr_branch_accepts_valid(branch: str) -> None:
    TaskCompletedPayload(
        task_id="t-01234567-89ab-7def-8abc-0123456789ab",
        summary="done",
        pr_branch=branch,
    )


# ---------------------------------------------------------------------------
# pr_branch pattern — invalid inputs
# ---------------------------------------------------------------------------

_INVALID_PR_BRANCHES = [
    "-leading-dash",  # starts with -
    ".leading-dot",  # starts with .
    "trailing/",  # ends with /
    "trailing.",  # ends with .
    "has spaces",  # spaces
    "has~tilde",  # tilde disallowed
    "has^caret",  # caret disallowed
    "has:colon",  # colon disallowed
    "has?question",  # question mark disallowed
    "has*asterisk",  # asterisk disallowed
    "has[bracket",  # bracket disallowed
    "has\\backslash",  # backslash disallowed
    "",  # empty
    "has..double-dots",  # .. sequence (git-check-ref-format)
    "double//slash",  # // sequence (git-check-ref-format)
    "feat/.hidden",  # /. sequence (git-check-ref-format)
    "branch.lock",  # .lock suffix (git-check-ref-format)
]


@pytest.mark.parametrize("branch", _INVALID_PR_BRANCHES)
def test_pr_branch_rejects_invalid(branch: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        TaskCompletedPayload(
            task_id="t-01234567-89ab-7def-8abc-0123456789ab",
            summary="done",
            pr_branch=branch,
        )


def test_pr_branch_none_is_valid() -> None:
    """pr_branch is optional — None should be accepted."""
    payload = TaskCompletedPayload(
        task_id="t-01234567-89ab-7def-8abc-0123456789ab",
        summary="done",
        pr_branch=None,
    )
    assert payload.pr_branch is None


# ---------------------------------------------------------------------------
# TaskPlanReadyPayload.plan — list→tuple coercion (Story 11.3.4)
#
# The model is strict=True with ``plan: tuple[PlanStep, ...]``. strict mode
# does NOT coerce list→tuple, but payloads cross the MCP/JSON boundary as
# arrays = Python lists (clawhip-bridge emit_event → EventEnvelope.create →
# model_validate). Without the mode="before" coercion, any plan-bearing
# task.plan.ready is rejected at emit with tuple_type — the latent production
# bug the S-1/S-2 separability harness surfaced. The read path is unaffected
# (from_canonical_json yields a _FrozenDict, no strict re-validation).
# ---------------------------------------------------------------------------

_GOOD_TASK_ID = "t-01234567-89ab-7def-8abc-0123456789ab"


def test_plan_ready_accepts_list_plan_from_json() -> None:
    """A JSON-origin list of step dicts must be accepted and stored as a tuple."""
    payload = TaskPlanReadyPayload.model_validate(
        {
            "task_id": _GOOD_TASK_ID,
            "plan_summary": "two steps",
            "plan": [
                {"step": 1, "description": "first"},
                {"step": 2, "description": "second"},
            ],
            "estimated_steps": 2,
        }
    )
    assert isinstance(payload.plan, tuple)
    assert len(payload.plan) == 2
    assert all(isinstance(s, PlanStep) for s in payload.plan)
    assert payload.plan[0].step == 1


def test_plan_ready_accepts_tuple_plan_unchanged() -> None:
    """Tuple input still validates (coercion is a no-op for tuples)."""
    payload = TaskPlanReadyPayload(
        task_id=_GOOD_TASK_ID,
        plan_summary="one step",
        plan=(PlanStep(step=1, description="only"),),
        estimated_steps=1,
    )
    assert isinstance(payload.plan, tuple)
    assert payload.plan[0].description == "only"


def test_plan_ready_default_empty_plan() -> None:
    """Omitting plan keeps the empty-tuple default (additive-only contract)."""
    payload = TaskPlanReadyPayload(task_id=_GOOD_TASK_ID, plan_summary="none")
    assert payload.plan == ()


def test_plan_ready_empty_list_coerces_to_empty_tuple() -> None:
    """A JSON empty array must coerce to the empty tuple, not be rejected."""
    payload = TaskPlanReadyPayload.model_validate(
        {"task_id": _GOOD_TASK_ID, "plan_summary": "none", "plan": []}
    )
    assert payload.plan == ()


def test_plan_ready_still_rejects_invalid_step_under_strict() -> None:
    """Coercion only fixes the container shape — element validation stays strict."""
    with pytest.raises(ValueError):
        TaskPlanReadyPayload.model_validate(
            {
                "task_id": _GOOD_TASK_ID,
                "plan_summary": "bad",
                "plan": [{"step": 0, "description": "step must be >= 1"}],
            }
        )


def test_plan_ready_rejects_explicit_null_plan() -> None:
    """Explicit JSON null for ``plan`` is rejected fail-loud — NOT coerced (11.3.4 review).

    ``model_dump()`` never emits null for ``plan`` (empty -> ``[]``, populated ->
    list of dicts), so a ``"plan": null`` payload is malformed. The mode="before"
    coercion only converts lists, leaving ``None`` to strict tuple validation,
    which rejects it. Pinning this documents the deliberate fail-loud boundary
    rather than silently treating null as the empty-tuple default (which would
    mask a producer bug). The default empty-tuple applies only when the key is
    ABSENT (see test_plan_ready_default_empty_plan).
    """
    with pytest.raises(ValueError):
        TaskPlanReadyPayload.model_validate(
            {"task_id": _GOOD_TASK_ID, "plan_summary": "x", "plan": None}
        )
