"""Tests for BudgetOverridePayload (Story 6.11)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from events.payloads import BudgetOverridePayload


def test_valid_payload() -> None:
    p = BudgetOverridePayload(
        task_id="t-01234567-89ab-7def-8000-000000000001",
        decision_id="d-01234567-89ab-7def-8000-000000000001",
        actor_id="operator-1",
        old_limit=50_000,
        new_limit=100_000,
    )
    assert p.old_limit == 50_000
    assert p.new_limit == 100_000


def test_frozen() -> None:
    p = BudgetOverridePayload(
        task_id="t-01234567-89ab-7def-8000-000000000001",
        decision_id="d-01234567-89ab-7def-8000-000000000001",
        actor_id="operator-1",
        old_limit=50_000,
        new_limit=100_000,
    )
    with pytest.raises(ValidationError):
        p.new_limit = 200_000


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        BudgetOverridePayload(
            task_id="t-01234567-89ab-7def-8000-000000000001",
            decision_id="d-01234567-89ab-7def-8000-000000000001",
            actor_id="operator-1",
            old_limit=50_000,
            new_limit=100_000,
            unexpected="field",  # type: ignore[call-arg]  # extra="forbid" rejects at runtime; testing exactly that
        )


def test_zero_limit_rejected() -> None:
    with pytest.raises(ValidationError):
        BudgetOverridePayload(
            task_id="t-01234567-89ab-7def-8000-000000000001",
            decision_id="d-01234567-89ab-7def-8000-000000000001",
            actor_id="operator-1",
            old_limit=0,
            new_limit=100_000,
        )


def test_negative_limit_rejected() -> None:
    with pytest.raises(ValidationError):
        BudgetOverridePayload(
            task_id="t-01234567-89ab-7def-8000-000000000001",
            decision_id="d-01234567-89ab-7def-8000-000000000001",
            actor_id="operator-1",
            old_limit=50_000,
            new_limit=-1,
        )


def test_empty_task_id_rejected() -> None:
    with pytest.raises(ValidationError):
        BudgetOverridePayload(
            task_id="",
            decision_id="d-01234567-89ab-7def-8000-000000000001",
            actor_id="operator-1",
            old_limit=50_000,
            new_limit=100_000,
        )


def test_new_limit_equal_to_old_rejected() -> None:
    with pytest.raises(ValidationError, match="new_limit must exceed old_limit"):
        BudgetOverridePayload(
            task_id="t-01234567-89ab-7def-8000-000000000001",
            decision_id="d-01234567-89ab-7def-8000-000000000001",
            actor_id="operator-1",
            old_limit=50_000,
            new_limit=50_000,
        )


def test_new_limit_less_than_old_rejected() -> None:
    with pytest.raises(ValidationError, match="new_limit must exceed old_limit"):
        BudgetOverridePayload(
            task_id="t-01234567-89ab-7def-8000-000000000001",
            decision_id="d-01234567-89ab-7def-8000-000000000001",
            actor_id="operator-1",
            old_limit=100_000,
            new_limit=50_000,
        )


def test_new_limit_upper_bound() -> None:
    with pytest.raises(ValidationError):
        BudgetOverridePayload(
            task_id="t-01234567-89ab-7def-8000-000000000001",
            decision_id="d-01234567-89ab-7def-8000-000000000001",
            actor_id="operator-1",
            old_limit=1_000_000_000,
            new_limit=1_000_000_001,
        )
