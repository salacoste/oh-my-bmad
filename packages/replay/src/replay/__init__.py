"""Historical event replay engine for oh-my-bmad (Phase 12 / ADR-0024).

Provides :func:`replay_events` which reconstructs point-in-time state from
the append-only JSONL event log by replaying events through the same
Materializer + handlers used by the live subscriber.

Read-only guarantee (P12-I1): replay operates on an in-memory SQLite database
that is discarded after each call — no write-path side effects on the live
database.
"""

from __future__ import annotations

from replay.archive_manifest import HOT_ONLY_REPLAY, HotOnlyReplaySentinel, SegmentKey
from replay.engine import replay_events
from replay.errors import (
    ReplayArchiveChecksumError,
    ReplayArchiveConfigError,
    ReplayArchiveConflictError,
    ReplayArchiveError,
    ReplayArchiveManifestError,
    ReplayArchiveMissingSegmentError,
)
from replay.lifecycle import (
    LifecycleArchiveCoverage,
    LifecycleBlocker,
    LifecycleDecision,
    LifecycleDryRunPlan,
    LifecycleRetentionPolicy,
    LifecycleSegmentIdentity,
    create_lifecycle_dry_run_plan,
)
from replay.models import ReplayMemoryError, ReplayMetadata, ReplayProgress, ReplayResult
from replay.retention import (
    RetentionBlocker,
    RetentionDecision,
    RetentionDomainPolicy,
    RetentionDryRunPlan,
    RetentionObjectIdentity,
    RetentionPlanError,
    RetentionPolicy,
    create_retention_dry_run_plan,
)
from replay.snapshots import (
    SnapshotInfo,
    create_snapshot,
    find_nearest_snapshot,
    list_snapshots,
    load_snapshot,
)
from replay.streaming import replay_events_stream
from replay.validation import (
    ValidationFieldDiff,
    ValidationResult,
    validate_replay,
)

__all__ = [
    "HOT_ONLY_REPLAY",
    "HotOnlyReplaySentinel",
    "ReplayArchiveChecksumError",
    "ReplayArchiveConfigError",
    "ReplayArchiveConflictError",
    "ReplayArchiveError",
    "ReplayArchiveManifestError",
    "ReplayArchiveMissingSegmentError",
    "ReplayProgress",
    "SegmentKey",
    "ReplayMemoryError",
    "ReplayMetadata",
    "ReplayResult",
    "LifecycleArchiveCoverage",
    "LifecycleBlocker",
    "LifecycleDecision",
    "LifecycleDryRunPlan",
    "LifecycleRetentionPolicy",
    "LifecycleSegmentIdentity",
    "RetentionPolicy",
    "RetentionPlanError",
    "RetentionObjectIdentity",
    "RetentionDryRunPlan",
    "RetentionDomainPolicy",
    "RetentionDecision",
    "RetentionBlocker",
    "SnapshotInfo",
    "ValidationFieldDiff",
    "ValidationResult",
    "create_snapshot",
    "create_lifecycle_dry_run_plan",
    "create_retention_dry_run_plan",
    "find_nearest_snapshot",
    "list_snapshots",
    "load_snapshot",
    "replay_events",
    "replay_events_stream",
    "validate_replay",
]
