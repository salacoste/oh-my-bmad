"""Budget extension policies for spend-limit recalculation.

Provides a small enum of extension strategies and a pure function that
computes the new limit from an old limit and a chosen policy.
"""

from __future__ import annotations

from enum import Enum


class BudgetExtendPolicy(Enum):
    DOUBLE = "double"
    PLUS_50K = "plus50k"
    MIN_OF_BOTH = "min_of_both"


def calculate_new_limit(
    old_limit: int,
    policy: BudgetExtendPolicy = BudgetExtendPolicy.MIN_OF_BOTH,
) -> int:
    if old_limit <= 0:
        raise ValueError(f"old_limit must be positive, got {old_limit}")

    if policy is BudgetExtendPolicy.DOUBLE:
        new_limit = old_limit * 2
    elif policy is BudgetExtendPolicy.PLUS_50K:
        new_limit = old_limit + 50_000
    else:
        new_limit = min(old_limit * 2, old_limit + 50_000)

    if new_limit <= old_limit:
        raise ValueError(f"new_limit ({new_limit}) must exceed old_limit ({old_limit})")

    return new_limit
