"""Shared test fixtures for console-cli tests.

Centralises literal values that were previously duplicated across
``test_decision_commands.py``, ``test_events_command.py`` and
``test_task_command.py``. Co-located with production code per the
project's single-writer / co-located test policy.
"""

from __future__ import annotations

from typing import Final

# Canonical fake bare-UUIDv7 trace_id used by X-Trace-Id propagation tests.
# Must match the bare-UUIDv7 branch of Story 9.1's ``is_valid_trace_id``
# contract (i.e. NOT prefixed with ``tg:``).
FAKE_TRACE_ID_UUIDV7: Final[str] = "01917e5c-a7d1-7000-8abc-0123456789ab"

# Regex pattern for a bare UUIDv7 (version-7 / RFC 9562 §5.7).
# Used by tests asserting the SHAPE of dynamically minted trace_ids.
UUIDV7_BARE_RE_PATTERN: Final[str] = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

__all__ = ["FAKE_TRACE_ID_UUIDV7", "UUIDV7_BARE_RE_PATTERN"]
