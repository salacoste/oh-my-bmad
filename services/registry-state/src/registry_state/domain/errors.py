"""Domain exceptions for registry_state (Story 2.5).

``MaterializerError`` is raised when an event cannot be applied to the
current state — typically because of an out-of-order replay (e.g.
``task.planning.started`` arrives before ``task.created``). The subscriber
loop logs + re-raises → process exits → Docker restart → replay from
beginning. Phase-1 does not retry in-loop; crash-recovery is the safety net.
"""

from __future__ import annotations


class MaterializerError(Exception):
    """Raised when the materializer cannot apply an event to the current state.

    Attributes:
        event_id:   The ``event_id`` of the envelope that triggered the error.
        event_type: The ``type`` field of the offending envelope.
        reason:     Human-readable explanation of why the event could not be applied.
    """

    def __init__(self, *, event_id: str, event_type: str, reason: str) -> None:
        super().__init__(f"MaterializerError [{event_type}] {event_id}: {reason}")
        self.event_id = event_id
        self.event_type = event_type
        self.reason = reason


__all__ = ["MaterializerError"]
