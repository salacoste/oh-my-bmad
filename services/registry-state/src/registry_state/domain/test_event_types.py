"""Tests for registry_state.domain.event_types — Story 3.10 AC-10.

4 tests covering the additive Story 3.10 fields on
:class:`TaskApprovalRequestedPayload` and the new :class:`PreCheckOutcome` /
:class:`DiffSummary` models:

1. v1.0.0 back-compat — old shape (task_id/action/justification only) parses
   cleanly with all four new optional fields defaulting to ``None``.
2. ``PreCheckOutcome`` rejects negative ``passed`` / ``total`` (Field(ge=0)).
3. ``DiffSummary`` rejects negative ``files`` / ``insertions`` / ``deletions``.
4. ``risk_class`` Literal rejects values outside ``{"low","medium","high"}``.
"""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import ValidationError

from registry_state.domain.event_types import (
    DiffSummary,
    PreCheckOutcome,
    TaskApprovalRequestedPayload,
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
