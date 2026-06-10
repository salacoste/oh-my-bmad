"""Data models for the replay engine (Phase 12 / Story 60-1).

Immutable dataclasses and domain errors used by :func:`replay.engine.replay_events`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReplayMetadata:
    """Summary statistics for a completed replay run."""

    event_count: int
    sequence_start: int  # first event's emitted_at_monotonic_ns
    sequence_end: int  # last event's emitted_at_monotonic_ns
    replay_duration_s: float
    snapshot_source: str | None  # None = full replay, or snapshot ID


@dataclass(frozen=True)
class ReplayResult:
    """Final output of a replay run: materialized state + metadata.

    ``state`` is a read-only snapshot — never written to the live database
    (P12-I1 read-only guarantee).
    """

    state: dict[str, Any]  # materialized state snapshot
    metadata: ReplayMetadata


class ReplayMemoryError(Exception):
    """Raised when replay exceeds the configured memory budget (NFR-R17)."""

    def __init__(self, current_bytes: int, limit_bytes: int) -> None:
        self.current_bytes = current_bytes
        self.limit_bytes = limit_bytes
        super().__init__(f"Replay memory limit exceeded: {current_bytes} > {limit_bytes}")


__all__ = [
    "ReplayMemoryError",
    "ReplayMetadata",
    "ReplayResult",
]
