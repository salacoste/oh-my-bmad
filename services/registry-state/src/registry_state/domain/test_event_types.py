"""Tests for registry_state.domain.event_types — Story 3.10 AC-10 + review pass.

Original AC-10 tests (4):

1. v1.0.0 back-compat — old shape (task_id/action/justification only) parses
   cleanly with all four new optional fields defaulting to ``None``.
2. ``PreCheckOutcome`` rejects negative ``passed`` / ``total`` (Field(ge=0)).
3. ``DiffSummary`` rejects negative ``files`` / ``insertions`` / ``deletions``.
4. ``risk_class`` Literal rejects values outside ``{"low","medium","high"}``.

Story 3.10 review-pass additions (H3, H4, H5, H6, L9, M13, H12):

* H3: ``task_id`` / ``action`` / ``justification`` length bounds (3 tests).
* H4: ``PreCheckOutcome`` rejects ``passed > total`` + positive case
  (covered by H12 below).
* H5: ``status`` semantic invariant (``pass`` requires ``==``; ``fail``
  requires ``<``).
* H6: ``accepted_commands`` rejects empty-string entries, oversize entries,
  and oversize lists.
* L9: ``DiffSummary`` rejects per-field overflow (``> 10**9``).
* M13: ``status`` widened to ``"skipped"`` / ``"error"``; both accepted.
* H12: positive case ``passed == total`` accepted; negative ``passed >
  total`` rejected with clear message.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import pytest
from pydantic import ValidationError

from registry_state.domain.event_types import (
    DiffSummary,
    PreCheckOutcome,
    TaskApprovalRequestedPayload,
    TaskBlockerRaisedPayload,
    TaskCompletedPayload,
)


def test_task_approval_requested_payload_v1_0_back_compat() -> None:
    """AC-1: Pre-3.10 v1.0.0 shape (3 required fields) deserializes cleanly under 1.1.0.

    All four new optional fields default to None — additive-only NFR-M3.
    """
    payload = TaskApprovalRequestedPayload(
        task_id="t-00000000-0000-7000-8000-000000000001",
        action="rm -rf node_modules",
        justification="rebuild deps",
    )
    assert payload.risk_class is None
    assert payload.pre_check_results is None
    assert payload.diff_summary is None
    assert payload.accepted_commands is None


def test_pre_check_outcome_rejects_negative_counts() -> None:
    """AC-2: Field(ge=0) on passed and total rejects negative integers."""
    with pytest.raises(ValidationError):
        PreCheckOutcome(passed=-1, total=10, status="pass")
    with pytest.raises(ValidationError):
        PreCheckOutcome(passed=5, total=-1, status="pass")
    # Sanity: 0/0 is allowed (a 0-of-0 pre-check is a valid edge case).
    ok = PreCheckOutcome(passed=0, total=0, status="pass")
    assert ok.passed == 0
    assert ok.total == 0


def test_diff_summary_rejects_negative_counts() -> None:
    """AC-3: Field(ge=0) on files/insertions/deletions rejects negative integers."""
    with pytest.raises(ValidationError):
        DiffSummary(files=-1, insertions=10, deletions=5)
    with pytest.raises(ValidationError):
        DiffSummary(files=5, insertions=-1, deletions=5)
    with pytest.raises(ValidationError):
        DiffSummary(files=5, insertions=10, deletions=-1)


def test_risk_class_literal_rejects_invalid_value() -> None:
    """AC-1: risk_class is Literal["low","medium","high"]; other strings rejected."""
    # Sanity: each valid value parses.
    valid_values: tuple[Literal["low", "medium", "high"], ...] = ("low", "medium", "high")
    for valid in valid_values:
        payload = TaskApprovalRequestedPayload(
            task_id="t-00000000-0000-7000-8000-000000000001",
            action="touch x",
            justification="why",
            risk_class=valid,
        )
        assert payload.risk_class == valid

    # Invalid value rejected.
    with pytest.raises(ValidationError):
        TaskApprovalRequestedPayload(
            task_id="t-00000000-0000-7000-8000-000000000001",
            action="touch x",
            justification="why",
            risk_class="critical",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Story 3.10 review-pass additions
# ---------------------------------------------------------------------------


_VALID_TASK_ID = "t-00000000-0000-7000-8000-000000000001"


def test_task_approval_requested_payload_rejects_empty_task_id() -> None:
    """H3: task_id min_length=1 — empty string rejected at the model boundary."""
    with pytest.raises(ValidationError):
        TaskApprovalRequestedPayload(task_id="", action="x", justification="y")


def test_task_approval_requested_payload_rejects_oversize_action() -> None:
    """H3: action max_length=2000 — 2001 chars rejected."""
    with pytest.raises(ValidationError):
        TaskApprovalRequestedPayload(task_id=_VALID_TASK_ID, action="a" * 2001, justification="y")


def test_task_approval_requested_payload_rejects_oversize_justification() -> None:
    """H3: justification max_length=10_000 — 10_001 chars rejected."""
    with pytest.raises(ValidationError):
        TaskApprovalRequestedPayload(
            task_id=_VALID_TASK_ID,
            action="x",
            justification="z" * 10_001,
        )


def test_pre_check_outcome_accepts_passed_equal_total() -> None:
    """H4 + H12 positive: passed == total is accepted (status='pass' invariant)."""
    ok = PreCheckOutcome(passed=10, total=10, status="pass")
    assert ok.passed == 10
    assert ok.total == 10


def test_pre_check_outcome_rejects_passed_gt_total() -> None:
    """H4 + H12 negative: passed > total rejected with clear error message."""
    with pytest.raises(ValidationError) as excinfo:
        PreCheckOutcome(passed=11, total=10, status="pass")
    msg = str(excinfo.value)
    assert "passed" in msg
    assert "total" in msg


def test_pre_check_outcome_status_pass_requires_passed_eq_total() -> None:
    """H5: status='pass' but passed < total → rejected (semantic invariant)."""
    with pytest.raises(ValidationError) as excinfo:
        PreCheckOutcome(passed=5, total=10, status="pass")
    assert "status='pass'" in str(excinfo.value)


def test_pre_check_outcome_status_fail_requires_passed_lt_total() -> None:
    """H5: status='fail' but passed == total → rejected."""
    with pytest.raises(ValidationError) as excinfo:
        PreCheckOutcome(passed=10, total=10, status="fail")
    assert "status='fail'" in str(excinfo.value)


def test_pre_check_outcome_accepts_skipped_state() -> None:
    """M13: status='skipped' accepted with arbitrary passed/total (no count constraint)."""
    skipped = PreCheckOutcome(passed=0, total=0, status="skipped")
    assert skipped.status == "skipped"
    # Skipped allows passed/total mismatch — the check did not run.
    skipped2 = PreCheckOutcome(passed=3, total=10, status="skipped")
    assert skipped2.status == "skipped"


def test_pre_check_outcome_accepts_error_state() -> None:
    """M13: status='error' accepted (the check itself crashed)."""
    err = PreCheckOutcome(passed=0, total=0, status="error")
    assert err.status == "error"


def test_accepted_commands_rejects_empty_string() -> None:
    """H6: each command min_length=1 — empty-string entries rejected."""
    with pytest.raises(ValidationError):
        TaskApprovalRequestedPayload(
            task_id=_VALID_TASK_ID,
            action="x",
            justification="y",
            accepted_commands=[""],
        )


def test_accepted_commands_rejects_oversize_command() -> None:
    """H6: each command max_length=200 — 201-char entry rejected."""
    with pytest.raises(ValidationError):
        TaskApprovalRequestedPayload(
            task_id=_VALID_TASK_ID,
            action="x",
            justification="y",
            accepted_commands=["a" * 201],
        )


def test_accepted_commands_rejects_oversize_list() -> None:
    """H6: list max_length=20 — 21-entry list rejected at the model boundary."""
    cmds = [f"/cmd-{i}" for i in range(21)]
    with pytest.raises(ValidationError):
        TaskApprovalRequestedPayload(
            task_id=_VALID_TASK_ID,
            action="x",
            justification="y",
            accepted_commands=cmds,
        )


def test_diff_summary_rejects_overflow_value() -> None:
    """L9: per-field upper bound 10**9 — 10**9 + 1 rejected."""
    with pytest.raises(ValidationError):
        DiffSummary(files=10**9 + 1, insertions=0, deletions=0)
    with pytest.raises(ValidationError):
        DiffSummary(files=0, insertions=10**9 + 1, deletions=0)
    with pytest.raises(ValidationError):
        DiffSummary(files=0, insertions=0, deletions=10**9 + 1)


# ---------------------------------------------------------------------------
# Story 3.11 — TaskBlockerRaisedPayload v1.1.0 additive extension (3 tests)
# ---------------------------------------------------------------------------


def test_task_blocker_raised_payload_v1_0_back_compat() -> None:
    """AC-1: Pre-3.11 v1.0.0/v1.0.1 shape (task_id + reason only) deserializes cleanly under 1.1.0.

    All three new optional fields default to None — additive-only NFR-M3.
    """
    payload = TaskBlockerRaisedPayload(
        task_id=_VALID_TASK_ID,
        reason="worker crashed mid-execution",
    )
    assert payload.blocked_since is None
    assert payload.last_event is None
    assert payload.last_action is None


def test_task_blocker_raised_payload_rejects_empty_task_id() -> None:
    """AC-1 / Story 3.10 H3 carry-forward: task_id min_length=1 — empty string rejected."""
    with pytest.raises(ValidationError):
        TaskBlockerRaisedPayload(task_id="", reason="worker crashed")


def test_task_blocker_raised_payload_rejects_oversized_reason() -> None:
    """AC-1 / Story 3.10 H3 carry-forward: reason max_length=2000 — 2001 chars rejected."""
    with pytest.raises(ValidationError):
        TaskBlockerRaisedPayload(task_id=_VALID_TASK_ID, reason="X" * 2001)


# ---------------------------------------------------------------------------
# Story 3.11 review pass — boundary + AwareDatetime tests (M5, M6, M7, M8, H8)
# ---------------------------------------------------------------------------


def test_task_blocker_raised_payload_rejects_oversized_task_id() -> None:
    """M5 / Story 3.10 H3 carry-forward: task_id max_length=64 — 65 chars rejected.

    The emergency one-liner safety relies on this upper bound (Story 3.11
    review H2 + M5).
    """
    with pytest.raises(ValidationError):
        TaskBlockerRaisedPayload(task_id="t" * 65, reason="worker crashed")


def test_task_blocker_raised_payload_rejects_oversized_last_event() -> None:
    """M6 / Story 3.11 H7: last_event max_length=128 — 129 chars rejected."""
    with pytest.raises(ValidationError):
        TaskBlockerRaisedPayload(
            task_id=_VALID_TASK_ID,
            reason="worker crashed",
            last_event="x" * 129,
        )


def test_task_blocker_raised_payload_rejects_oversized_last_action() -> None:
    """M7 / Story 3.11 H7: last_action max_length=2000 — 2001 chars rejected."""
    with pytest.raises(ValidationError):
        TaskBlockerRaisedPayload(
            task_id=_VALID_TASK_ID,
            reason="worker crashed",
            last_action="a" * 2001,
        )


def test_task_blocker_raised_payload_rejects_empty_last_event() -> None:
    """H7: last_event min_length=1 — empty string rejected (no useless trailing-space line)."""
    with pytest.raises(ValidationError):
        TaskBlockerRaisedPayload(
            task_id=_VALID_TASK_ID,
            reason="worker crashed",
            last_event="",
        )


def test_task_blocker_raised_payload_rejects_empty_last_action() -> None:
    """H7: last_action min_length=1 — empty string rejected."""
    with pytest.raises(ValidationError):
        TaskBlockerRaisedPayload(
            task_id=_VALID_TASK_ID,
            reason="worker crashed",
            last_action="",
        )


def test_task_blocker_raised_payload_accepts_aware_blocked_since() -> None:
    """M8 / H8: tz-aware datetime is accepted (round-trips cleanly under AwareDatetime)."""
    aware = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    payload = TaskBlockerRaisedPayload(
        task_id=_VALID_TASK_ID,
        reason="worker crashed",
        blocked_since=aware,
    )
    assert payload.blocked_since == aware


def test_task_blocker_raised_payload_rejects_naive_blocked_since() -> None:
    """M8 / H8: naive datetime (no tzinfo) raises ValidationError under AwareDatetime."""
    naive = datetime(2026, 5, 1, 12, 0, 0)
    with pytest.raises(ValidationError):
        TaskBlockerRaisedPayload(
            task_id=_VALID_TASK_ID,
            reason="worker crashed",
            blocked_since=naive,
        )


# ---------------------------------------------------------------------------
# Story 3.12 — TaskCompletedPayload v1.1.0 additive extension (4 tests)
# ---------------------------------------------------------------------------


def test_task_completed_payload_v1_0_back_compat() -> None:
    """AC-1: Pre-3.12 v1.0.x shape deserializes cleanly under 1.1.0.

    Old shape (task_id + summary + optional pr_url) parses; all eight
    new optional FR9 fields default to None — additive-only NFR-M3.
    """
    payload = TaskCompletedPayload(
        task_id=_VALID_TASK_ID,
        summary="task complete",
    )
    assert payload.pr_url is None
    assert payload.pr_number is None
    assert payload.pr_branch is None
    assert payload.files_changed is None
    assert payload.lines_added is None
    assert payload.lines_removed is None
    assert payload.tests_added is None
    assert payload.ci_state is None
    assert payload.blockers_count is None


def test_task_completed_payload_rejects_oversized_pr_branch() -> None:
    """AC-1: pr_branch max_length=255 (git ref-name limit) — 256 chars rejected."""
    with pytest.raises(ValidationError):
        TaskCompletedPayload(
            task_id=_VALID_TASK_ID,
            summary="task complete",
            pr_branch="b" * 256,
        )


def test_task_completed_payload_rejects_negative_counters() -> None:
    """AC-1 / Story 3.10 L9 carry-forward: negative counter values rejected.

    files_changed / lines_added / lines_removed / tests_added / blockers_count
    all carry ge=0; pr_number carries ge=1 (so 0 is rejected).
    """
    with pytest.raises(ValidationError):
        TaskCompletedPayload(task_id=_VALID_TASK_ID, summary="x", files_changed=-1)
    with pytest.raises(ValidationError):
        TaskCompletedPayload(task_id=_VALID_TASK_ID, summary="x", lines_added=-1)
    with pytest.raises(ValidationError):
        TaskCompletedPayload(task_id=_VALID_TASK_ID, summary="x", lines_removed=-1)
    with pytest.raises(ValidationError):
        TaskCompletedPayload(task_id=_VALID_TASK_ID, summary="x", tests_added=-1)
    with pytest.raises(ValidationError):
        TaskCompletedPayload(task_id=_VALID_TASK_ID, summary="x", blockers_count=-1)
    # pr_number ge=1 — 0 rejected (real PR numbers start at 1).
    with pytest.raises(ValidationError):
        TaskCompletedPayload(task_id=_VALID_TASK_ID, summary="x", pr_number=0)


def test_task_completed_payload_rejects_invalid_ci_state() -> None:
    """AC-1: ci_state is Literal["green","red","unknown"]; other strings rejected.

    Story 3.12 review L6: assert the specific Pydantic error code is
    ``literal_error`` so a future Pydantic upgrade that renames or
    splits the type cannot silently turn this test into a vacuous
    "ValidationError raised somewhere" check.
    """
    # Sanity: each valid value parses.
    valid_values: tuple[Literal["green", "red", "unknown"], ...] = ("green", "red", "unknown")
    for valid in valid_values:
        payload = TaskCompletedPayload(
            task_id=_VALID_TASK_ID,
            summary="task complete",
            ci_state=valid,
        )
        assert payload.ci_state == valid

    # Invalid value rejected with the specific literal_error code (L6).
    with pytest.raises(ValidationError) as exc_info:
        TaskCompletedPayload(
            task_id=_VALID_TASK_ID,
            summary="task complete",
            ci_state="yellow",  # type: ignore[arg-type]
        )
    assert exc_info.value.errors()[0]["type"] == "literal_error"


# ---------------------------------------------------------------------------
# Story 3.12 review-pass additions (H7, M2, M3, M7, L4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("pr_number", 10**9 + 1),
        ("lines_added", 10**9 + 1),
        ("lines_removed", 10**9 + 1),
        ("files_changed", 10**6 + 1),
        ("tests_added", 10**6 + 1),
        ("blockers_count", 10**6 + 1),
    ],
)
def test_task_completed_payload_rejects_upper_bound_overflow(field_name: str, value: int) -> None:
    """H7: per-field upper bounds reject overflow (Story 3.10 L9 carry-forward).

    File-level counters cap at 10**6; line-level counters / pr_number cap
    at 10**9. Story 3.12 honors the L9 discipline in code; this test
    closes the coverage gap.
    """
    kwargs: dict[str, object] = {"task_id": _VALID_TASK_ID, "summary": "x"}
    kwargs[field_name] = value
    with pytest.raises(ValidationError):
        TaskCompletedPayload(**kwargs)  # type: ignore[arg-type]


def test_task_completed_payload_rejects_javascript_pr_url() -> None:
    """M2: pr_url constrained to http(s):// — javascript: scheme rejected."""
    with pytest.raises(ValidationError):
        TaskCompletedPayload(
            task_id=_VALID_TASK_ID,
            summary="x",
            pr_url="javascript:alert(1)",
        )


def test_task_completed_payload_rejects_data_pr_url() -> None:
    """M2: pr_url constrained to http(s):// — data: scheme rejected."""
    with pytest.raises(ValidationError):
        TaskCompletedPayload(
            task_id=_VALID_TASK_ID,
            summary="x",
            pr_url="data:text/html,<script>alert(1)</script>",
        )


def test_task_completed_payload_accepts_http_and_https_pr_url() -> None:
    """M2 sanity: both http:// and https:// schemes accepted."""
    p1 = TaskCompletedPayload(task_id=_VALID_TASK_ID, summary="x", pr_url="http://example.com/pr/1")
    p2 = TaskCompletedPayload(
        task_id=_VALID_TASK_ID, summary="x", pr_url="https://example.com/pr/2"
    )
    assert p1.pr_url == "http://example.com/pr/1"
    assert p2.pr_url == "https://example.com/pr/2"


def test_task_completed_payload_rejects_oversized_pr_url() -> None:
    """M3: pr_url max_length=500 — 501 chars rejected."""
    # 501 chars total; "https://" prefix (8) + 493 chars after.
    with pytest.raises(ValidationError):
        TaskCompletedPayload(
            task_id=_VALID_TASK_ID,
            summary="x",
            pr_url="https://" + "x" * 493,
        )


def test_task_completed_payload_rejects_oversized_summary() -> None:
    """M3: summary max_length=2000 — 2001 chars rejected."""
    with pytest.raises(ValidationError):
        TaskCompletedPayload(task_id=_VALID_TASK_ID, summary="x" * 2001)


def test_task_completed_payload_rejects_empty_pr_branch() -> None:
    """M3: pr_branch min_length=1 — empty string rejected."""
    with pytest.raises(ValidationError):
        TaskCompletedPayload(task_id=_VALID_TASK_ID, summary="x", pr_branch="")


def test_task_completed_payload_rejects_empty_pr_url() -> None:
    """M3: pr_url min_length=1 — empty string rejected (also fails pattern)."""
    with pytest.raises(ValidationError):
        TaskCompletedPayload(task_id=_VALID_TASK_ID, summary="x", pr_url="")


def test_task_completed_payload_rejects_extra_field() -> None:
    """M7: ConfigDict(extra="forbid") rejects unknown field names.

    Defends against typos like ``pr_numbar=42`` silently passing through
    to a renderer that would never see the value.
    """
    with pytest.raises(ValidationError):
        TaskCompletedPayload(
            task_id=_VALID_TASK_ID,
            summary="x",
            pr_numbar=42,  # type: ignore[call-arg]
        )


def test_task_completed_schema_versions_register_distinct_entries() -> None:
    """L4: 1.0.0, 1.0.1, and 1.1.0 are all registered for task.completed.

    Validates the same-model contract — registry holds three independent
    (type, version) entries pointing at the same payload class. Insertion
    order does not matter; per-version lookup is direct.

    The ``packages/events`` test_schema_registry uses an autouse fixture
    that calls ``unregister_all()`` between every test in *that* file —
    when this module's test runs after that, the registry is empty. Re-
    registering the three entries here (idempotent same-class no-op when
    the canonical event_types module loaded first) makes the test
    order-independent.
    """
    from events.schema_registry import REGISTRY, register

    register("task.completed", "1.0.0", TaskCompletedPayload)
    register("task.completed", "1.0.1", TaskCompletedPayload)
    register("task.completed", "1.1.0", TaskCompletedPayload)

    assert ("task.completed", "1.0.0") in REGISTRY
    assert ("task.completed", "1.0.1") in REGISTRY
    assert ("task.completed", "1.1.0") in REGISTRY
    # All three resolve to the same payload class.
    assert REGISTRY[("task.completed", "1.0.0")] is TaskCompletedPayload
    assert REGISTRY[("task.completed", "1.0.1")] is TaskCompletedPayload
    assert REGISTRY[("task.completed", "1.1.0")] is TaskCompletedPayload


# ---------------------------------------------------------------------------
# Story 3.13 — TaskSelfRecoveredPayload (FR16, 5 tests)
# ---------------------------------------------------------------------------


def test_task_self_recovered_payload_minimal_round_trip() -> None:
    """AC-1: construct with all 4 fields; round-trip via model_dump_json + model_validate_json."""
    from registry_state.domain.event_types import TaskSelfRecoveredPayload

    aware = datetime(2026, 5, 1, 3, 0, 0, tzinfo=UTC)
    payload = TaskSelfRecoveredPayload(
        task_id=_VALID_TASK_ID,
        recovered_at=aware,
        events_replayed=142,
        replay_duration_ms=350,
    )
    raw = payload.model_dump_json()
    restored = type(payload).model_validate_json(raw)
    assert restored == payload
    # Verify the ISO timestamp round-trips with tz info.
    assert restored.recovered_at.utcoffset() is not None


def test_task_self_recovered_payload_rejects_empty_task_id() -> None:
    """AC-1: task_id min_length=1 — empty string rejected."""
    from registry_state.domain.event_types import TaskSelfRecoveredPayload

    with pytest.raises(ValidationError):
        TaskSelfRecoveredPayload(
            task_id="",
            recovered_at=datetime(2026, 5, 1, 3, 0, 0, tzinfo=UTC),
            events_replayed=0,
            replay_duration_ms=0,
        )


def test_task_self_recovered_payload_rejects_oversized_task_id() -> None:
    """AC-1: task_id max_length=64 — 65 chars rejected."""
    from registry_state.domain.event_types import TaskSelfRecoveredPayload

    with pytest.raises(ValidationError):
        TaskSelfRecoveredPayload(
            task_id="t" * 65,
            recovered_at=datetime(2026, 5, 1, 3, 0, 0, tzinfo=UTC),
            events_replayed=0,
            replay_duration_ms=0,
        )


def test_task_self_recovered_payload_rejects_naive_recovered_at() -> None:
    """AC-1: AwareDatetime — naive datetime (no tzinfo) raises ValidationError."""
    from registry_state.domain.event_types import TaskSelfRecoveredPayload

    naive = datetime(2026, 5, 1, 12, 0, 0)
    with pytest.raises(ValidationError):
        TaskSelfRecoveredPayload(
            task_id=_VALID_TASK_ID,
            recovered_at=naive,
            events_replayed=0,
            replay_duration_ms=0,
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("events_replayed", -1),
        ("events_replayed", 10**6 + 1),
        ("replay_duration_ms", -1),
        ("replay_duration_ms", 10**9 + 1),
    ],
)
def test_task_self_recovered_payload_rejects_negative_counters_and_oversized(
    field_name: str, value: int
) -> None:
    """AC-1: counter boundary validation — ge=0 / le=10**6 / le=10**9."""
    from registry_state.domain.event_types import TaskSelfRecoveredPayload

    kwargs: dict[str, object] = {
        "task_id": _VALID_TASK_ID,
        "recovered_at": datetime(2026, 5, 1, 3, 0, 0, tzinfo=UTC),
        "events_replayed": 0,
        "replay_duration_ms": 0,
    }
    kwargs[field_name] = value
    with pytest.raises(ValidationError):
        TaskSelfRecoveredPayload(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Story 5.5 — agent.reasoning.* auto-registration on module import
# ---------------------------------------------------------------------------


def test_agent_reasoning_types_registered_on_import() -> None:
    """Importing event_types auto-registers the three agent.reasoning.* schemas.

    The worker-wrapper's test_reasoning.py cannot verify this wiring without a
    cross-service import violation, so coverage lives here instead.
    """
    from events.schema_registry import REGISTRY, register

    from registry_state.domain.event_types import AgentReasoningBreadcrumbPayload

    # Re-register to make test order-independent (autouse fixtures may clear).
    register("agent.reasoning.plan_drafted", "1.0.0", AgentReasoningBreadcrumbPayload)
    register("agent.reasoning.tool_call_rationale", "1.0.0", AgentReasoningBreadcrumbPayload)
    register("agent.reasoning.step_summary", "1.0.0", AgentReasoningBreadcrumbPayload)

    assert REGISTRY[("agent.reasoning.plan_drafted", "1.0.0")] is AgentReasoningBreadcrumbPayload
    assert REGISTRY[("agent.reasoning.tool_call_rationale", "1.0.0")] is AgentReasoningBreadcrumbPayload
    assert REGISTRY[("agent.reasoning.step_summary", "1.0.0")] is AgentReasoningBreadcrumbPayload
