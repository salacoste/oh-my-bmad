"""Pure domain function for approval-gate detection (Story 6.7, AC-2).

Scans extracted events for Tier-3 actions requiring operator approval
before execution. This module has zero IO — it operates on data already
in memory. Uses ``typing.Protocol`` to avoid importing from the adapter
layer (DIP compliance).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class HasEventType(Protocol):
    """Minimal protocol for event objects with an ``event_type`` field."""

    event_type: str


def needs_approval[EventT: HasEventType](events: Sequence[EventT]) -> EventT | None:
    """Return the first ``git.push`` event requiring approval, or ``None``.

    Phase-1 policy: only ``git push`` triggers the approval gate.
    Future Tier-3 actions (e.g. ``npm publish``) can be added here
    without touching the adapter layer. Only the first ``git.push``
    event is returned — multiple pushes in a single session are a
    Phase-1 simplification (the second push executes without a
    separate approval).
    """
    for event in events:
        if event.event_type == "git.push":
            return event
    return None
