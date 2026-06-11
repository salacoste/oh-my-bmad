"""Non-destructive lifecycle dry-run planner for event-log hot segments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

from replay.archive_manifest import (
    ArchiveManifestInput,
    SegmentEvents,
    collect_hot_segments,
    load_archive_manifest,
)

SCHEMA_VERSION = 1
DEFAULT_SAFETY_POLICY_VERSION = "phase14.lifecycle.dry_run.v1"

SegmentSource = Literal["hot", "archive"]
DecisionStatus = Literal["retained", "eligible", "blocked"]


@dataclass(frozen=True)
class LifecycleRetentionPolicy:
    """Retention input captured in the content-addressed dry-run plan."""

    retain_hot_days: int
    now: str
    cutoff_date: str


@dataclass(frozen=True)
class LifecycleSegmentIdentity:
    """Stable segment identity used by lifecycle decisions."""

    source: SegmentSource
    logical_date: str
    first_sequence: int
    last_sequence: int
    original_relpath: str
    archive_relpath: str | None
    sha256: str
    event_count: int


@dataclass(frozen=True)
class LifecycleArchiveCoverage:
    """Archive coverage evidence for one hot segment."""

    matched: bool
    archive_segment: LifecycleSegmentIdentity | None
    reason: str


@dataclass(frozen=True)
class LifecycleDecision:
    """Dry-run decision for one hot segment."""

    status: DecisionStatus
    reason: str
    hot_segment: LifecycleSegmentIdentity
    archive_coverage: LifecycleArchiveCoverage


@dataclass(frozen=True)
class LifecycleBlocker:
    """Fail-closed blocker for a hot segment that is not archive-covered."""

    code: str
    message: str
    segment: LifecycleSegmentIdentity


@dataclass(frozen=True)
class LifecycleDryRunPlan:
    """Immutable content-addressed lifecycle dry-run plan."""

    schema_version: int
    safety_policy_version: str
    generated_at: str
    retention_policy: LifecycleRetentionPolicy
    decisions: tuple[LifecycleDecision, ...]
    blockers: tuple[LifecycleBlocker, ...]
    plan_hash: str

    def canonical_payload(self) -> dict[str, object]:
        """Return the deterministic payload covered by :attr:`plan_hash`."""
        return {
            "schema_version": self.schema_version,
            "safety_policy_version": self.safety_policy_version,
            "retention_policy": _retention_policy_payload(self.retention_policy),
            "decisions": [_decision_payload(decision) for decision in self.decisions],
            "blockers": [_blocker_payload(blocker) for blocker in self.blockers],
        }

    def canonical_json(self) -> str:
        """Return compact sorted-key JSON for the canonical payload."""
        return _canonical_json(self.canonical_payload())

    @property
    def artifact_filename(self) -> str:
        """Recommended operator artifact filename for this dry-run plan."""
        return f"lifecycle-dry-run-plan-{self.plan_hash}.json"


def create_lifecycle_dry_run_plan(
    *,
    event_log_dir: Path,
    archive_manifest_path: ArchiveManifestInput = None,
    retain_hot_days: int,
    now: datetime | None = None,
    safety_policy_version: str = DEFAULT_SAFETY_POLICY_VERSION,
) -> LifecycleDryRunPlan:
    """Create a non-destructive lifecycle dry-run plan for hot segments."""
    if retain_hot_days < 0:
        raise ValueError("retain_hot_days must be >= 0")
    effective_now = _normalize_now(now)
    cutoff = effective_now.date() - timedelta(days=retain_hot_days)
    retention_policy = LifecycleRetentionPolicy(
        retain_hot_days=retain_hot_days,
        now=_format_utc(effective_now),
        cutoff_date=cutoff.isoformat(),
    )
    hot_segments = collect_hot_segments(event_log_dir)
    archive_segments = load_archive_manifest(
        event_log_dir=event_log_dir,
        archive_manifest_path=archive_manifest_path,
    )
    archive_by_original = {segment.original_relpath: segment for segment in archive_segments}

    decisions: list[LifecycleDecision] = []
    blockers: list[LifecycleBlocker] = []
    for hot in hot_segments:
        hot_identity = _segment_identity(hot, source="hot")
        segment_date = date.fromisoformat(hot.key.logical_date)
        coverage = _archive_coverage(hot, archive_by_original.get(hot.original_relpath))
        if segment_date >= cutoff:
            decisions.append(
                LifecycleDecision(
                    status="retained",
                    reason="within_retention_window",
                    hot_segment=hot_identity,
                    archive_coverage=coverage,
                )
            )
            continue
        if coverage.matched:
            decisions.append(
                LifecycleDecision(
                    status="eligible",
                    reason="archive_coverage_verified",
                    hot_segment=hot_identity,
                    archive_coverage=coverage,
                )
            )
            continue

        blocker = LifecycleBlocker(
            code="archive_coverage_missing",
            message="old hot segment is not covered by a matching archive segment",
            segment=hot_identity,
        )
        blockers.append(blocker)
        decisions.append(
            LifecycleDecision(
                status="blocked",
                reason=blocker.code,
                hot_segment=hot_identity,
                archive_coverage=coverage,
            )
        )

    plan_without_hash = {
        "schema_version": SCHEMA_VERSION,
        "safety_policy_version": safety_policy_version,
        "retention_policy": _retention_policy_payload(retention_policy),
        "decisions": [_decision_payload(decision) for decision in decisions],
        "blockers": [_blocker_payload(blocker) for blocker in blockers],
    }
    plan_hash = _sha256_text(_canonical_json(plan_without_hash))
    return LifecycleDryRunPlan(
        schema_version=SCHEMA_VERSION,
        safety_policy_version=safety_policy_version,
        generated_at=_format_utc(effective_now),
        retention_policy=retention_policy,
        decisions=tuple(decisions),
        blockers=tuple(blockers),
        plan_hash=plan_hash,
    )


def _normalize_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _archive_coverage(
    hot: SegmentEvents,
    archive: SegmentEvents | None,
) -> LifecycleArchiveCoverage:
    if archive is None:
        return LifecycleArchiveCoverage(
            matched=False,
            archive_segment=None,
            reason="archive_coverage_missing",
        )
    archive_identity = _segment_identity(archive, source="archive")
    if (
        hot.key == archive.key
        and hot.sha256 == archive.sha256
        and hot.event_count == archive.event_count
        and hot.original_relpath == archive.original_relpath
    ):
        return LifecycleArchiveCoverage(
            matched=True,
            archive_segment=archive_identity,
            reason="archive_coverage_verified",
        )
    return LifecycleArchiveCoverage(
        matched=False,
        archive_segment=archive_identity,
        reason="archive_coverage_mismatch",
    )


def _segment_identity(
    segment: SegmentEvents,
    *,
    source: SegmentSource,
) -> LifecycleSegmentIdentity:
    return LifecycleSegmentIdentity(
        source=source,
        logical_date=segment.key.logical_date,
        first_sequence=segment.key.first_sequence,
        last_sequence=segment.key.last_sequence,
        original_relpath=segment.original_relpath,
        archive_relpath=segment.archive_relpath,
        sha256=segment.sha256,
        event_count=segment.event_count,
    )


def _retention_policy_payload(policy: LifecycleRetentionPolicy) -> dict[str, object]:
    return {
        "retain_hot_days": policy.retain_hot_days,
        "now": policy.now,
        "cutoff_date": policy.cutoff_date,
    }


def _segment_payload(segment: LifecycleSegmentIdentity) -> dict[str, object]:
    return {
        "source": segment.source,
        "logical_date": segment.logical_date,
        "first_sequence": segment.first_sequence,
        "last_sequence": segment.last_sequence,
        "original_relpath": segment.original_relpath,
        "archive_relpath": segment.archive_relpath,
        "sha256": segment.sha256,
        "event_count": segment.event_count,
    }


def _coverage_payload(coverage: LifecycleArchiveCoverage) -> dict[str, object]:
    archive_payload = (
        _segment_payload(coverage.archive_segment) if coverage.archive_segment is not None else None
    )
    return {
        "matched": coverage.matched,
        "archive_segment": archive_payload,
        "reason": coverage.reason,
    }


def _decision_payload(decision: LifecycleDecision) -> dict[str, object]:
    return {
        "status": decision.status,
        "reason": decision.reason,
        "hot_segment": _segment_payload(decision.hot_segment),
        "archive_coverage": _coverage_payload(decision.archive_coverage),
    }


def _blocker_payload(blocker: LifecycleBlocker) -> dict[str, object]:
    return {
        "code": blocker.code,
        "message": blocker.message,
        "segment": _segment_payload(blocker.segment),
    }


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "LifecycleArchiveCoverage",
    "LifecycleBlocker",
    "LifecycleDecision",
    "LifecycleDryRunPlan",
    "LifecycleRetentionPolicy",
    "LifecycleSegmentIdentity",
    "create_lifecycle_dry_run_plan",
]
