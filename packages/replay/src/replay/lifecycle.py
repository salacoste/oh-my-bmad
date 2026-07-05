"""Non-destructive lifecycle dry-run planner for event-log hot segments."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from replay.archive_manifest import (
    ArchiveManifestInput,
    SegmentEvents,
    collect_hot_segments,
    load_archive_manifest,
    resolve_archive_manifest_path,
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
    "LifecycleApprovalEvidence",
    "LifecycleArchiveCoverage",
    "LifecycleBlocker",
    "LifecycleDecision",
    "LifecycleDryRunArtifact",
    "LifecycleDryRunPlan",
    "LifecycleMutationError",
    "LifecycleMutationResult",
    "LifecycleRetentionPolicy",
    "LifecycleSegmentIdentity",
    "SUPPORTED_LIFECYCLE_MUTATION_CLASS",
    "UNSUPPORTED_LIFECYCLE_MUTATION_CLASSES",
    "apply_lifecycle_plan",
    "approve_lifecycle_plan",
    "create_lifecycle_dry_run_plan",
    "get_lifecycle_plan_status",
    "list_lifecycle_mutations",
    "record_lifecycle_dry_run",
    "rollback_lifecycle_plan",
]

# ---------------------------------------------------------------------------
# Epic 129: approval-bound destructive lifecycle mutation controls
# ---------------------------------------------------------------------------

DEFAULT_LIFECYCLE_EVIDENCE_DIRNAME = "lifecycle-evidence"
DEFAULT_LIFECYCLE_TRASH_DIRNAME = ".lifecycle-trash"
DEFAULT_LIFECYCLE_PLAN_TTL_SECONDS = 3600
SUPPORTED_LIFECYCLE_MUTATION_CLASS = "prune_hot_segment"
UNSUPPORTED_LIFECYCLE_MUTATION_CLASSES = frozenset(
    {
        "delete",
        "truncate",
        "move",
        "rewrite",
        "chmod",
        "archive_mutation",
        "manifest_mutation",
        "hard_delete",
        "object_storage_delete",
        "scheduled_retention",
    }
)


@dataclass
class LifecycleMutationError(Exception):
    """Fail-closed lifecycle mutation error with stable ProblemDetails code."""

    code: str
    message: str
    status_code: int = 409

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class LifecycleDryRunArtifact:
    """Persisted dry-run evidence for an approval-bound lifecycle plan."""

    plan_hash: str
    plan: LifecycleDryRunPlan
    generated_at: str
    expires_at: str
    expected_mutations: tuple[dict[str, object], ...]
    replay_validation_ref: str
    replay_validation_status: str
    rollback_evidence_ref: str
    risk_summary: str
    trace_id: str | None
    request_id: str | None
    artifact_path: str


@dataclass(frozen=True)
class LifecycleApprovalEvidence:
    """Operator approval bound to an exact lifecycle dry-run plan hash."""

    plan_hash: str
    operator_identity: str
    approved_at: str
    approval_event_ref: str
    expires_at: str


@dataclass(frozen=True)
class LifecycleMutationResult:
    """Result for apply/rollback/status operations."""

    plan_hash: str
    action: str
    status: str
    idempotency_key: str | None
    affected_count: int
    journal_path: str
    status_path: str
    replayed: bool = False
    problem_code: str | None = None
    detail: str | None = None


def record_lifecycle_dry_run(
    *,
    event_log_dir: Path,
    archive_manifest_path: ArchiveManifestInput = None,
    retain_hot_days: int,
    evidence_dir: Path | None = None,
    now: datetime | None = None,
    expires_in_seconds: int = DEFAULT_LIFECYCLE_PLAN_TTL_SECONDS,
    replay_validation_ref: str | None = None,
    replay_validation_status: str = "unverified",
    rollback_evidence_ref: str = "rollback:quarantine-restore-supported",
    trace_id: str | None = None,
    request_id: str | None = None,
) -> LifecycleDryRunArtifact:
    """Create and persist immutable dry-run evidence without mutating hot files."""
    effective_now = _normalize_now(now)
    if expires_in_seconds <= 0:
        raise LifecycleMutationError(
            code="invalid_expiry",
            message="expires_in_seconds must be > 0",
            status_code=422,
        )
    if replay_validation_status != "passed" or not (replay_validation_ref or "").strip():
        raise LifecycleMutationError(
            "replay_validation_not_current",
            "fresh passing replay validation evidence is required",
            403,
        )
    plan = create_lifecycle_dry_run_plan(
        event_log_dir=event_log_dir,
        archive_manifest_path=archive_manifest_path,
        retain_hot_days=retain_hot_days,
        now=effective_now,
    )
    resolved_manifest = resolve_archive_manifest_path(archive_manifest_path)
    root = _evidence_root(event_log_dir=event_log_dir, evidence_dir=evidence_dir)
    plan_dir = _plan_dir(root, plan.plan_hash)
    expires_at = _format_utc(effective_now + timedelta(seconds=expires_in_seconds))
    expected_mutations = _expected_mutations(plan)
    artifact_payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "plan_hash": plan.plan_hash,
        "generated_at": _format_utc(effective_now),
        "expires_at": expires_at,
        "canonical_plan": plan.canonical_payload(),
        "expected_mutations": expected_mutations,
        "archive_manifest_dir": str(resolved_manifest.parent) if resolved_manifest else None,
        "replay_validation_ref": replay_validation_ref,
        "replay_validation_status": replay_validation_status,
        "rollback_evidence_ref": rollback_evidence_ref,
        "risk_summary": _risk_summary(plan),
        "trace_id": trace_id,
        "request_id": request_id,
    }
    path = plan_dir / "plan.json"
    with _evidence_lock(root):
        if path.exists():
            existing = _load_plan_artifact(plan_dir)
            _assert_plan_hash_matches(existing, plan.plan_hash)
            return _artifact_from_payload(existing, path, plan)
        plan_dir.mkdir(parents=True, exist_ok=True)
        _write_json(path, artifact_payload)
        _append_journal(
            plan_dir,
            {
                "state": "dry_run_recorded",
                "action": "dry_run",
                "plan_hash": plan.plan_hash,
                "timestamp": _format_utc(effective_now),
                "result": "recorded",
                "affected_count": len(expected_mutations),
                "trace_id": trace_id,
                "request_id": request_id,
            },
        )
        _write_status(
            plan_dir,
            {
                "plan_hash": plan.plan_hash,
                "status": "dry_run_recorded",
                "plan_expires_at": expires_at,
                "approval_expires_at": None,
                "freshness_state": "fresh",
                "supported_mutation_class": SUPPORTED_LIFECYCLE_MUTATION_CLASS,
                "unsupported_mutation_classes": sorted(UNSUPPORTED_LIFECYCLE_MUTATION_CLASSES),
                "affected_count": len(expected_mutations),
                "approved": False,
                "applied": False,
                "rolled_back": False,
                "problem_code": None,
            },
        )
    return _artifact_from_payload(artifact_payload, path, plan)


def _artifact_from_payload(
    payload: dict[str, object], path: Path, plan: LifecycleDryRunPlan
) -> LifecycleDryRunArtifact:
    trace_id = payload.get("trace_id")
    request_id = payload.get("request_id")
    return LifecycleDryRunArtifact(
        plan_hash=str(payload["plan_hash"]),
        plan=plan,
        generated_at=str(payload["generated_at"]),
        expires_at=str(payload["expires_at"]),
        expected_mutations=tuple(_eligible_expected_mutations(payload)),
        replay_validation_ref=str(payload["replay_validation_ref"]),
        replay_validation_status=str(payload["replay_validation_status"]),
        rollback_evidence_ref=str(payload["rollback_evidence_ref"]),
        risk_summary=str(payload["risk_summary"]),
        trace_id=trace_id if isinstance(trace_id, str) else None,
        request_id=request_id if isinstance(request_id, str) else None,
        artifact_path=str(path),
    )


def approve_lifecycle_plan(
    *,
    event_log_dir: Path,
    plan_hash: str,
    operator_identity: str,
    approval_event_ref: str,
    evidence_dir: Path | None = None,
    now: datetime | None = None,
    expires_in_seconds: int = DEFAULT_LIFECYCLE_PLAN_TTL_SECONDS,
) -> LifecycleApprovalEvidence:
    """Persist approval evidence bound to an exact dry-run plan hash."""
    effective_now = _normalize_now(now)
    if expires_in_seconds <= 0:
        raise LifecycleMutationError(
            code="invalid_expiry",
            message="expires_in_seconds must be > 0",
            status_code=422,
        )
    if not operator_identity.strip() or not approval_event_ref.strip():
        raise LifecycleMutationError(
            code="approval_evidence_missing",
            message="operator_identity and approval_event_ref are required",
            status_code=422,
        )
    plan_dir = _existing_plan_dir(event_log_dir, evidence_dir, plan_hash)
    with _plan_lock(plan_dir):
        artifact = _load_plan_artifact(plan_dir)
        _assert_plan_hash_matches(artifact, plan_hash)
        _assert_not_expired(artifact["expires_at"], effective_now, code="plan_expired")
        expires_at = _format_utc(effective_now + timedelta(seconds=expires_in_seconds))
        approval: dict[str, object] = {
            "plan_hash": plan_hash,
            "operator_identity": operator_identity,
            "approved_at": _format_utc(effective_now),
            "approval_event_ref": approval_event_ref,
            "expires_at": expires_at,
        }
        _write_json(plan_dir / "approval.json", approval)
        _append_journal(
            plan_dir,
            {
                "state": "approved",
                "action": "approve",
                "plan_hash": plan_hash,
                "timestamp": approval["approved_at"],
                "operator_identity": operator_identity,
                "approval_event_ref": approval_event_ref,
                "result": "approved",
            },
        )
        status = _read_status(plan_dir)
        status.update(
            {
                "status": "approved",
                "approved": True,
                "operator_identity": operator_identity,
                "approval_event_ref": approval_event_ref,
                "approval_expires_at": expires_at,
                "freshness_state": "fresh",
            }
        )
        _write_status(plan_dir, status)
        return LifecycleApprovalEvidence(
            plan_hash=plan_hash,
            operator_identity=operator_identity,
            approved_at=str(approval["approved_at"]),
            approval_event_ref=approval_event_ref,
            expires_at=expires_at,
        )


def apply_lifecycle_plan(
    *,
    event_log_dir: Path,
    plan_hash: str,
    idempotency_key: str,
    evidence_dir: Path | None = None,
    now: datetime | None = None,
    mutation_class: str = SUPPORTED_LIFECYCLE_MUTATION_CLASS,
) -> LifecycleMutationResult:
    """Apply the supported lifecycle prune by moving eligible hot files to quarantine."""
    effective_now = _normalize_now(now)
    if not idempotency_key.strip():
        raise LifecycleMutationError("idempotency_key_missing", "idempotency_key is required", 422)
    if mutation_class != SUPPORTED_LIFECYCLE_MUTATION_CLASS:
        raise LifecycleMutationError(
            "unsupported_mutation_class",
            f"unsupported lifecycle mutation class: {mutation_class}",
            422,
        )
    plan_dir = _existing_plan_dir(event_log_dir, evidence_dir, plan_hash)
    with _mutation_lock(plan_dir), _plan_lock(plan_dir):
        replayed = _completed_result_for_key(plan_dir, action="apply", key=idempotency_key)
        if replayed is not None:
            return replayed
        artifact = _load_plan_artifact(plan_dir)
        approval = _load_approval(plan_dir)
        _preflight_apply(artifact, approval, plan_hash, effective_now)
        expected = _eligible_expected_mutations(artifact)
        if not expected:
            raise LifecycleMutationError("no_eligible_targets", "plan has no eligible targets", 409)
        _append_journal(
            plan_dir,
            _journal_event("apply_started", "apply", plan_hash, effective_now, idempotency_key),
        )
        _revalidate_hot_targets(event_log_dir, expected)
        _revalidate_archive_coverage(artifact)
        _append_journal(
            plan_dir,
            _journal_event(
                "target_revalidated",
                "apply",
                plan_hash,
                effective_now,
                idempotency_key,
                affected_count=len(expected),
            ),
        )
        quarantine = event_log_dir / DEFAULT_LIFECYCLE_TRASH_DIRNAME / plan_hash
        quarantine.mkdir(parents=True, exist_ok=True)
        moved: list[dict[str, object]] = []
        try:
            for item in expected:
                original = event_log_dir / str(item["original_relpath"])
                dest = quarantine / str(item["original_relpath"])
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(original), str(dest))
                moved.append({**item, "quarantine_relpath": str(dest.relative_to(event_log_dir))})
        except Exception as exc:  # noqa: BLE001 - convert partial mutation to fail-closed status
            if _restore_moved_targets(event_log_dir, moved):
                _record_problem(
                    plan_dir,
                    plan_hash,
                    "apply_failed_restored",
                    "apply_failed_restored",
                    str(exc),
                    effective_now,
                    idempotency_key,
                )
                raise LifecycleMutationError(
                    "apply_failed_restored",
                    "apply failed and moved files were restored",
                    409,
                ) from exc
            _record_problem(
                plan_dir,
                plan_hash,
                "apply_failed_partial",
                "apply_failed_partial",
                str(exc),
                effective_now,
                idempotency_key,
                partial_moved=moved,
            )
            raise LifecycleMutationError(
                "apply_failed_partial",
                "apply failed after partial filesystem changes; rollback is required",
                409,
            ) from exc
        _append_journal(
            plan_dir,
            {
                **_journal_event(
                    "quarantined",
                    "apply",
                    plan_hash,
                    effective_now,
                    idempotency_key,
                    affected_count=len(moved),
                ),
                "moved": moved,
            },
        )
        result = LifecycleMutationResult(
            plan_hash=plan_hash,
            action="apply",
            status="apply_succeeded",
            idempotency_key=idempotency_key,
            affected_count=len(moved),
            journal_path=str(plan_dir / "journal.jsonl"),
            status_path=str(plan_dir / "status.json"),
        )
        _append_journal(
            plan_dir,
            _journal_event(
                "apply_succeeded",
                "apply",
                plan_hash,
                effective_now,
                idempotency_key,
                affected_count=len(moved),
            ),
        )
        status = _read_status(plan_dir)
        status.update(
            {
                "status": "apply_succeeded",
                "applied": True,
                "rolled_back": False,
                "affected_count": len(moved),
                "last_idempotency_key": idempotency_key,
                "problem_code": None,
            }
        )
        _write_status(plan_dir, status)
        return result


def rollback_lifecycle_plan(
    *,
    event_log_dir: Path,
    plan_hash: str,
    idempotency_key: str,
    rollback_event_ref: str,
    evidence_dir: Path | None = None,
    now: datetime | None = None,
) -> LifecycleMutationResult:
    """Restore quarantined hot files for a previously applied lifecycle plan."""
    effective_now = _normalize_now(now)
    if not idempotency_key.strip() or not rollback_event_ref.strip():
        raise LifecycleMutationError(
            "rollback_evidence_missing",
            "idempotency_key and rollback_event_ref are required",
            422,
        )
    plan_dir = _existing_plan_dir(event_log_dir, evidence_dir, plan_hash)
    with _mutation_lock(plan_dir), _plan_lock(plan_dir):
        replayed = _completed_result_for_key(plan_dir, action="rollback", key=idempotency_key)
        if replayed is not None:
            return replayed
        status = _read_status(plan_dir)
        if status.get("applied") is not True and status.get("status") != "apply_failed_partial":
            raise LifecycleMutationError("not_applied", "plan has not been applied", 409)
        artifact = _load_plan_artifact(plan_dir)
        _assert_plan_hash_matches(artifact, plan_hash)
        expected = _eligible_expected_mutations(artifact)
        _append_journal(
            plan_dir,
            {
                **_journal_event(
                    "rollback_started",
                    "rollback",
                    plan_hash,
                    effective_now,
                    idempotency_key,
                ),
                "rollback_event_ref": rollback_event_ref,
            },
        )
        quarantine = event_log_dir / DEFAULT_LIFECYCLE_TRASH_DIRNAME / plan_hash
        restored: list[dict[str, object]] = []
        try:
            for item in expected:
                dest = event_log_dir / str(item["original_relpath"])
                source = quarantine / str(item["original_relpath"])
                if dest.exists() and _sha256_path(dest) == item["sha256"]:
                    restored.append(item)
                    continue
                if dest.exists():
                    raise LifecycleMutationError(
                        "rollback_target_drift",
                        f"hot target changed since apply: {item['original_relpath']}",
                    )
                if not source.exists():
                    raise FileNotFoundError(str(source))
                if _sha256_path(source) != item["sha256"]:
                    raise LifecycleMutationError(
                        "rollback_hash_mismatch",
                        f"quarantined segment hash mismatch: {item['original_relpath']}",
                    )
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(dest))
                restored.append(item)
        except Exception as exc:  # noqa: BLE001
            _record_problem(
                plan_dir,
                plan_hash,
                "rollback_failed_partial",
                exc.code if isinstance(exc, LifecycleMutationError) else "rollback_failed_partial",
                str(exc),
                effective_now,
                idempotency_key,
            )
            if isinstance(exc, LifecycleMutationError):
                raise
            raise LifecycleMutationError(
                "rollback_failed_partial",
                "rollback failed after partial filesystem changes; manual reconciliation required",
                409,
            ) from exc
        _append_journal(
            plan_dir,
            _journal_event(
                "restored",
                "rollback",
                plan_hash,
                effective_now,
                idempotency_key,
                affected_count=len(restored),
            ),
        )
        result = LifecycleMutationResult(
            plan_hash=plan_hash,
            action="rollback",
            status="rollback_succeeded",
            idempotency_key=idempotency_key,
            affected_count=len(restored),
            journal_path=str(plan_dir / "journal.jsonl"),
            status_path=str(plan_dir / "status.json"),
        )
        _append_journal(
            plan_dir,
            {
                **_journal_event(
                    "rollback_succeeded",
                    "rollback",
                    plan_hash,
                    effective_now,
                    idempotency_key,
                    affected_count=len(restored),
                ),
                "rollback_event_ref": rollback_event_ref,
            },
        )
        status.update(
            {
                "status": "rollback_succeeded",
                "rolled_back": True,
                "problem_code": None,
                "last_rollback_idempotency_key": idempotency_key,
            }
        )
        _write_status(plan_dir, status)
        return result


def get_lifecycle_plan_status(
    *,
    event_log_dir: Path,
    plan_hash: str,
    evidence_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    """Read lifecycle plan status and audit summary without mutating state."""
    plan_dir = _existing_plan_dir(event_log_dir, evidence_dir, plan_hash)
    status = _status_with_freshness(plan_dir, _normalize_now(now))
    status["journal"] = _read_journal(plan_dir)
    return status


def list_lifecycle_mutations(
    *, event_log_dir: Path, evidence_dir: Path | None = None, now: datetime | None = None
) -> list[dict[str, object]]:
    """List all persisted lifecycle plan statuses without mutating state."""
    root = _evidence_root(event_log_dir=event_log_dir, evidence_dir=evidence_dir)
    plans = root / "plans"
    if not plans.exists():
        return []
    rows = []
    effective_now = _normalize_now(now)
    for status_path in sorted(plans.glob("*/status.json")):
        rows.append(_public_status_row(_status_with_freshness(status_path.parent, effective_now)))
    return rows


def _evidence_root(*, event_log_dir: Path, evidence_dir: Path | None) -> Path:
    if evidence_dir is not None:
        event_root = event_log_dir.resolve()
        resolved = evidence_dir.resolve()
        if resolved != event_root and event_root not in resolved.parents:
            raise LifecycleMutationError(
                "evidence_dir_outside_event_log",
                "lifecycle evidence directory must stay under event_log_dir",
                422,
            )
        return resolved
    return event_log_dir.resolve() / DEFAULT_LIFECYCLE_EVIDENCE_DIRNAME


def _plan_dir(root: Path, plan_hash: str) -> Path:
    _validate_plan_hash(plan_hash)
    return root / "plans" / plan_hash


def _existing_plan_dir(event_log_dir: Path, evidence_dir: Path | None, plan_hash: str) -> Path:
    root = _evidence_root(event_log_dir=event_log_dir, evidence_dir=evidence_dir)
    plan_dir = _plan_dir(root, plan_hash)
    if not (plan_dir / "plan.json").is_file():
        raise LifecycleMutationError(
            "plan_not_found",
            f"lifecycle plan not found: {plan_hash}",
            404,
        )
    return plan_dir


def _validate_plan_hash(plan_hash: str) -> None:
    if len(plan_hash) != 64 or any(ch not in "0123456789abcdef" for ch in plan_hash):
        raise LifecycleMutationError(
            "invalid_plan_hash",
            "plan_hash must be 64 lowercase hex chars",
            422,
        )


@contextmanager
def _plan_lock(plan_dir: Path) -> Iterator[None]:
    locks = plan_dir.parent.parent / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    lock_dir = locks / f"{plan_dir.name}.lock"
    try:
        os.mkdir(lock_dir)
    except FileExistsError as exc:
        raise LifecycleMutationError(
            "plan_locked",
            "lifecycle plan is currently locked",
            409,
        ) from exc
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_dir.rmdir()


@contextmanager
def _evidence_lock(root: Path) -> Iterator[None]:
    locks = root / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    lock_dir = locks / "evidence-write.lock"
    try:
        os.mkdir(lock_dir)
    except FileExistsError as exc:
        raise LifecycleMutationError(
            "lifecycle_evidence_locked",
            "lifecycle evidence namespace is currently locked",
            409,
        ) from exc
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_dir.rmdir()


@contextmanager
def _mutation_lock(plan_dir: Path) -> Iterator[None]:
    locks = plan_dir.parent.parent / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    lock_dir = locks / "event-log-lifecycle-mutation.lock"
    try:
        os.mkdir(lock_dir)
    except FileExistsError as exc:
        raise LifecycleMutationError(
            "lifecycle_mutation_locked",
            "event log lifecycle mutation is currently locked",
            409,
        ) from exc
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_dir.rmdir()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, object]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise LifecycleMutationError("lifecycle_evidence_invalid", "JSON object expected")
    return cast(dict[str, object], payload)


def _append_journal(plan_dir: Path, event: dict[str, object]) -> None:
    event = {"schema_version": SCHEMA_VERSION, **event}
    with (plan_dir / "journal.jsonl").open("a", encoding="utf-8") as f:
        f.write(_canonical_json(event) + "\n")


def _read_journal(plan_dir: Path) -> list[dict[str, object]]:
    path = plan_dir / "journal.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_status(plan_dir: Path, status: dict[str, object]) -> None:
    _write_json(plan_dir / "status.json", status)


def _read_status(plan_dir: Path) -> dict[str, object]:
    path = plan_dir / "status.json"
    return _read_json(path) if path.exists() else {"plan_hash": plan_dir.name, "status": "unknown"}


def _status_with_freshness(plan_dir: Path, now: datetime) -> dict[str, object]:
    status = _read_status(plan_dir)
    plan_path = plan_dir / "plan.json"
    if plan_path.exists():
        artifact = _read_json(plan_path)
        status["plan_expires_at"] = artifact.get("expires_at")
    else:
        status.setdefault("plan_expires_at", None)
    approval_path = plan_dir / "approval.json"
    if approval_path.exists():
        approval = _read_json(approval_path)
        status["approval_expires_at"] = approval.get("expires_at")
    else:
        status.setdefault("approval_expires_at", None)
    status["freshness_state"] = _freshness_state(status, now)
    return status


def _freshness_state(status: dict[str, object], now: datetime) -> str:
    for key in ("plan_expires_at", "approval_expires_at"):
        value = status.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            return "stale"
        try:
            expires_at = _parse_utc(value)
        except ValueError:
            return "stale"
        if expires_at < now:
            return "stale"
    return "fresh"


def _public_status_row(status: dict[str, object]) -> dict[str, object]:
    allowed = (
        "plan_hash",
        "status",
        "affected_count",
        "approved",
        "applied",
        "rolled_back",
        "supported_mutation_class",
        "unsupported_mutation_classes",
        "plan_expires_at",
        "approval_expires_at",
        "freshness_state",
        "problem_code",
    )
    return {key: status[key] for key in allowed if key in status}


def _load_plan_artifact(plan_dir: Path) -> dict[str, object]:
    return _read_json(plan_dir / "plan.json")


def _load_approval(plan_dir: Path) -> dict[str, object]:
    path = plan_dir / "approval.json"
    if not path.exists():
        raise LifecycleMutationError("approval_missing", "approval evidence is missing", 403)
    return _read_json(path)


def _assert_plan_hash_matches(artifact: dict[str, object], plan_hash: str) -> None:
    if artifact.get("plan_hash") != plan_hash:
        raise LifecycleMutationError(
            "plan_hash_mismatch",
            "persisted plan hash does not match request",
            409,
        )
    canonical = artifact.get("canonical_plan")
    if not isinstance(canonical, dict):
        raise LifecycleMutationError("plan_artifact_invalid", "canonical plan payload missing", 409)
    recomputed = _sha256_text(_canonical_json(canonical))
    if recomputed != plan_hash:
        raise LifecycleMutationError("plan_hash_mismatch", "canonical payload hash mismatch", 409)


def _assert_not_expired(expires_at: object, now: datetime, *, code: str) -> None:
    if not isinstance(expires_at, str):
        raise LifecycleMutationError(code, "expiry timestamp missing", 409)
    if _parse_utc(expires_at) < now:
        raise LifecycleMutationError(code, "lifecycle evidence is expired", 409)


def _parse_utc(value: str) -> datetime:
    iso_value = (value[:-1] + "+00:00") if value.endswith("Z") else value
    return datetime.fromisoformat(iso_value).astimezone(UTC)


def _json_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _preflight_apply(
    artifact: dict[str, object], approval: dict[str, object], plan_hash: str, now: datetime
) -> None:
    _assert_plan_hash_matches(artifact, plan_hash)
    if artifact.get("replay_validation_status") != "passed":
        raise LifecycleMutationError(
            "replay_validation_not_current",
            "replay validation is not passing",
        )
    if not artifact.get("rollback_evidence_ref"):
        raise LifecycleMutationError("rollback_evidence_missing", "rollback evidence is missing")
    canonical_plan = artifact.get("canonical_plan")
    if isinstance(canonical_plan, dict) and canonical_plan.get("blockers"):
        raise LifecycleMutationError("plan_has_blockers", "plan has fail-closed blockers")
    _assert_not_expired(artifact.get("expires_at"), now, code="plan_expired")
    if approval.get("plan_hash") != plan_hash:
        raise LifecycleMutationError(
            "approval_plan_hash_mismatch",
            "approval is bound to another plan",
        )
    if not approval.get("operator_identity") or not approval.get("approval_event_ref"):
        raise LifecycleMutationError(
            "approval_evidence_missing",
            "approval evidence is incomplete",
            403,
        )
    _assert_not_expired(approval.get("expires_at"), now, code="approval_expired")


def _expected_mutations(plan: LifecycleDryRunPlan) -> list[dict[str, object]]:
    mutations: list[dict[str, object]] = []
    for decision in plan.decisions:
        if decision.status != "eligible":
            continue
        segment = _segment_payload(decision.hot_segment)
        mutations.append(
            {
                "mutation_class": SUPPORTED_LIFECYCLE_MUTATION_CLASS,
                "original_relpath": segment["original_relpath"],
                "logical_date": segment["logical_date"],
                "first_sequence": segment["first_sequence"],
                "last_sequence": segment["last_sequence"],
                "sha256": segment["sha256"],
                "event_count": segment["event_count"],
            }
        )
    return mutations


def _eligible_expected_mutations(artifact: dict[str, object]) -> list[dict[str, object]]:
    canonical = artifact.get("canonical_plan")
    if not isinstance(canonical, dict):
        raise LifecycleMutationError("plan_artifact_invalid", "canonical plan payload missing")
    derived = _expected_mutations_from_canonical(canonical)
    expected = artifact.get("expected_mutations")
    if not isinstance(expected, list):
        raise LifecycleMutationError("expected_mutations_missing", "expected mutations missing")
    if expected != derived:
        raise LifecycleMutationError(
            "expected_mutations_mismatch",
            "expected mutations do not match canonical lifecycle plan",
        )
    return derived


def _expected_mutations_from_canonical(canonical: dict[str, object]) -> list[dict[str, object]]:
    decisions = canonical.get("decisions")
    if not isinstance(decisions, list):
        raise LifecycleMutationError("plan_artifact_invalid", "canonical decisions missing")
    mutations: list[dict[str, object]] = []
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("status") != "eligible":
            continue
        segment = decision.get("hot_segment")
        if not isinstance(segment, dict):
            raise LifecycleMutationError("plan_artifact_invalid", "canonical segment missing")
        mutations.append(
            {
                "mutation_class": SUPPORTED_LIFECYCLE_MUTATION_CLASS,
                "original_relpath": segment["original_relpath"],
                "logical_date": segment["logical_date"],
                "first_sequence": segment["first_sequence"],
                "last_sequence": segment["last_sequence"],
                "sha256": segment["sha256"],
                "event_count": segment["event_count"],
            }
        )
    return mutations


def _risk_summary(plan: LifecycleDryRunPlan) -> str:
    eligible = sum(1 for decision in plan.decisions if decision.status == "eligible")
    retained = sum(1 for decision in plan.decisions if decision.status == "retained")
    blocked = sum(1 for decision in plan.decisions if decision.status == "blocked")
    return (
        f"eligible={eligible}; retained={retained}; blocked={blocked}; "
        "supported_action=prune_hot_segment_quarantine_restore"
    )


def _revalidate_hot_targets(event_log_dir: Path, expected: list[dict[str, object]]) -> None:
    for item in expected:
        path = event_log_dir / str(item["original_relpath"])
        if not path.is_file():
            raise LifecycleMutationError(
                "target_missing",
                f"target segment missing: {item['original_relpath']}",
            )
        if _sha256_path(path) != item["sha256"]:
            raise LifecycleMutationError(
                "target_hash_mismatch",
                f"target hash mismatch: {item['original_relpath']}",
            )


def _revalidate_archive_coverage(artifact: dict[str, object]) -> None:
    canonical = artifact.get("canonical_plan")
    if not isinstance(canonical, dict):
        raise LifecycleMutationError("plan_artifact_invalid", "canonical plan payload missing")
    decisions = canonical.get("decisions")
    if not isinstance(decisions, list):
        raise LifecycleMutationError("plan_artifact_invalid", "canonical decisions missing")
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("status") != "eligible":
            continue
        coverage = decision.get("archive_coverage")
        if not isinstance(coverage, dict) or coverage.get("matched") is not True:
            raise LifecycleMutationError(
                "archive_coverage_not_current",
                "eligible segment archive coverage is missing",
            )
        archive_segment = coverage.get("archive_segment")
        if not isinstance(archive_segment, dict):
            raise LifecycleMutationError(
                "archive_coverage_not_current",
                "eligible segment archive coverage is missing",
            )
        archive_path_value = archive_segment.get("archive_relpath")
        if not isinstance(archive_path_value, str):
            raise LifecycleMutationError(
                "archive_coverage_not_current",
                "eligible segment archive path is missing",
            )
        archive_root_value = artifact.get("archive_manifest_dir")
        if not isinstance(archive_root_value, str):
            raise LifecycleMutationError(
                "archive_coverage_not_current",
                "archive manifest directory is missing",
            )
        archive_path = Path(archive_root_value) / archive_path_value
        if not archive_path.is_file():
            raise LifecycleMutationError(
                "archive_coverage_not_current",
                f"archive segment missing: {archive_path_value}",
            )
        if _sha256_path(archive_path) != archive_segment.get("sha256"):
            raise LifecycleMutationError(
                "archive_coverage_not_current",
                f"archive segment hash mismatch: {archive_path_value}",
            )


def _restore_moved_targets(event_log_dir: Path, moved: list[dict[str, object]]) -> bool:
    try:
        for item in reversed(moved):
            original = event_log_dir / str(item["original_relpath"])
            source = event_log_dir / str(item["quarantine_relpath"])
            if original.exists() and _sha256_path(original) == item["sha256"]:
                continue
            if original.exists():
                return False
            if not source.exists():
                return False
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(original))
            if _sha256_path(original) != item["sha256"]:
                return False
    except Exception:  # noqa: BLE001 - restoration status is converted to fail-closed result
        return False
    return True


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _journal_event(
    state: str,
    action: str,
    plan_hash: str,
    now: datetime,
    idempotency_key: str | None,
    *,
    affected_count: int = 0,
) -> dict[str, object]:
    return {
        "state": state,
        "action": action,
        "plan_hash": plan_hash,
        "timestamp": _format_utc(now),
        "idempotency_key": idempotency_key,
        "affected_count": affected_count,
        "result": state,
    }


def _record_problem(
    plan_dir: Path,
    plan_hash: str,
    state: str,
    code: str,
    detail: str,
    now: datetime,
    idempotency_key: str | None,
    partial_moved: list[dict[str, object]] | None = None,
) -> None:
    _append_journal(
        plan_dir,
        {
            **_journal_event(state, state.split("_")[0], plan_hash, now, idempotency_key),
            "result": "blocked",
            "problem_code": code,
            "detail": detail,
        },
    )
    status = _read_status(plan_dir)
    update: dict[str, object] = {"status": state, "problem_code": code, "detail": detail}
    if partial_moved is not None:
        update["partial_moved"] = partial_moved
    status.update(update)
    _write_status(plan_dir, status)


def _completed_result_for_key(
    plan_dir: Path, *, action: str, key: str
) -> LifecycleMutationResult | None:
    terminal = "apply_succeeded" if action == "apply" else "rollback_succeeded"
    for event in reversed(_read_journal(plan_dir)):
        if event.get("action") == action and event.get("idempotency_key") == key:
            if event.get("state") == terminal:
                return LifecycleMutationResult(
                    plan_hash=plan_dir.name,
                    action=action,
                    status=terminal,
                    idempotency_key=key,
                    affected_count=_json_int(event.get("affected_count"), default=0),
                    journal_path=str(plan_dir / "journal.jsonl"),
                    status_path=str(plan_dir / "status.json"),
                    replayed=True,
                )
            raise LifecycleMutationError(
                "manual_reconciliation_required",
                "idempotency key is associated with an incomplete lifecycle operation",
                409,
            )
    return None
