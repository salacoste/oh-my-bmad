"""Top-level pytest fixtures — cross-cutting (clock, UUIDv7) live here.

Real fixture bodies arrive per-story:
  - fixed_clock    — Story 2.1 (packages/events/src/events/clock.py)
  - seeded_uuid7   — Story 2.2 (packages/events/src/events/ids.py)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest


@pytest.fixture
def fixed_clock() -> Any:
    raise NotImplementedError(
        "fixed_clock arrives with packages/events/src/events/clock.py in Story 2.1"
    )


@pytest.fixture
def seeded_uuid7() -> Any:
    raise NotImplementedError(
        "seeded_uuid7 arrives with packages/events/src/events/ids.py in Story 2.2"
    )


# Re-exported so a test may `from tests.conftest import FROZEN_EPOCH` once the
# real fixtures land. Keep deterministic across the Phase-1 test run.
FROZEN_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
