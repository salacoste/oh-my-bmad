"""Pytest fixtures for ``packages/events`` (Story 10.2 pass-3 P3-L3).

Wires module-global warn-state resets between tests so the one-shot
WARN-once guards do not silently break test ordering.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from events.log_reader import _reset_warn_state_for_tests as _reset_events_warn


@pytest.fixture(autouse=True)
def _reset_module_global_warn_flags() -> Generator[None, None, None]:
    """P3-L3 — reset module-global one-shot warn flags between tests.

    The ``_MAX_EVENTS_EXCEEDS_LINE_CAP_WARNED`` flag in
    ``events.log_reader`` is per-process by design (the warn signals
    a caller configuration mistake, not a per-call event).  But that
    makes test ordering fragile — once one test triggers the warn,
    no subsequent test in the same pytest session can observe it.
    This autouse fixture resets the flag before each test.

    Test-only; the helper must NOT be called in production code.
    """
    _reset_events_warn()
    yield
