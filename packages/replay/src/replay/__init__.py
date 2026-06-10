"""Historical event replay engine for oh-my-bmad (Phase 12 / ADR-0024).

Provides :func:`replay_events` which reconstructs point-in-time state from
the append-only JSONL event log by replaying events through the same
Materializer + handlers used by the live subscriber.

Read-only guarantee (P12-I1): replay operates on an in-memory SQLite database
that is discarded after each call — no write-path side effects on the live
database.
"""

from __future__ import annotations

from replay.engine import replay_events
from replay.models import ReplayMemoryError, ReplayMetadata, ReplayResult
from replay.snapshots import (
    SnapshotInfo,
    create_snapshot,
    find_nearest_snapshot,
    list_snapshots,
    load_snapshot,
)
from replay.validation import (
    ValidationFieldDiff,
    ValidationResult,
    validate_replay,
)

__all__ = [
    "ReplayMemoryError",
    "ReplayMetadata",
    "ReplayResult",
    "SnapshotInfo",
    "ValidationFieldDiff",
    "ValidationResult",
    "create_snapshot",
    "find_nearest_snapshot",
    "list_snapshots",
    "load_snapshot",
    "replay_events",
    "validate_replay",
]
