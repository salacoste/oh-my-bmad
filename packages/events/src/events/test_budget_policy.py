"""Unit tests for events.budget_policy — BudgetExtendPolicy + calculate_new_limit."""

from __future__ import annotations

import pytest

from events.budget_policy import BudgetExtendPolicy, calculate_new_limit


class TestDoublePolicy:
    def test_double_policy(self) -> None:
        assert calculate_new_limit(100, policy=BudgetExtendPolicy.DOUBLE) == 200

    def test_double_large_value(self) -> None:
        assert calculate_new_limit(1_000_000, policy=BudgetExtendPolicy.DOUBLE) == 2_000_000


class TestPlus50kPolicy:
    def test_plus50k_policy(self) -> None:
        assert calculate_new_limit(100, policy=BudgetExtendPolicy.PLUS_50K) == 50_100

    def test_plus50k_large_value(self) -> None:
        assert calculate_new_limit(1_000_000, policy=BudgetExtendPolicy.PLUS_50K) == 1_050_000


class TestMinOfBothPolicy:
    def test_min_of_both_chooses_double_when_lower(self) -> None:
        # old_limit=10: double=20, plus50k=50_010 → min=20
        assert calculate_new_limit(10, policy=BudgetExtendPolicy.MIN_OF_BOTH) == 20

    def test_min_of_both_chooses_plus50k_when_lower(self) -> None:
        # old_limit=1_000_000: double=2_000_000, plus50k=1_050_000 → min=1_050_000
        assert (
            calculate_new_limit(1_000_000, policy=BudgetExtendPolicy.MIN_OF_BOTH)
            == 1_050_000
        )

    def test_min_of_both_equal_when_same(self) -> None:
        # old_limit=50_000: double=100_000, plus50k=100_000 → equal
        assert calculate_new_limit(50_000, policy=BudgetExtendPolicy.MIN_OF_BOTH) == 100_000


class TestValidation:
    def test_zero_limit_raises(self) -> None:
        with pytest.raises(ValueError, match="old_limit must be positive"):
            calculate_new_limit(0)

    def test_negative_limit_raises(self) -> None:
        with pytest.raises(ValueError, match="old_limit must be positive"):
            calculate_new_limit(-1)

    def test_default_policy_is_min_of_both(self) -> None:
        # Calling without explicit policy uses MIN_OF_BOTH
        assert calculate_new_limit(10) == 20
