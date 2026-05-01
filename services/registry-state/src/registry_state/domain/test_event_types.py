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
