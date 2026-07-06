"""Read-only retention observability projection (Story 130.5).

The helpers in this module summarize already-produced retention runner/apply
records. They do not call the dry-run planner, apply adapter, scheduler,
credentials, object storage, command surfaces, or runtime audit emitters.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final

from replay.retention_apply import RetentionApplyRecord
from replay.retention_runner import RetentionRunnerRecord

STATUS_POLICY_VERSION: Final = "story130.retention.status.v1"

_SECRET_MARKERS: Final = (
    "secret",
    "token",
    "credential",
    "password",
    "private_key",
    "access_key",
)


@dataclass(frozen=True)
class RetentionStatusProjection:
    """Public read-only retention status for operator docs/status surfaces."""

    enabled: bool
    status_policy_version: str = STATUS_POLICY_VERSION
    next_run_at: str | None = None
    last_run_at: str | None = None
    failure_count: int = 0
    skipped_protected_object_count: int = 0
    audit_count: int = 0
    degraded: bool = False
    degraded_reasons: tuple[str, ...] = ()
    runner_status_counts: Mapping[str, int] = field(default_factory=dict)
    apply_status_counts: Mapping[str, int] = field(default_factory=dict)
    retention_capability: str = "package-local-default-disabled"
    live_scheduler_active: bool = False
    credential_loaded: bool = False
    mutation_triggered_by_status: bool = False

    def to_public_dict(self) -> dict[str, object]:
        """Return a secret-free dictionary for docs/API/status rendering."""

        return {
            "enabled": self.enabled,
            "status_policy_version": self.status_policy_version,
            "next_run_at": self.next_run_at,
            "last_run_at": self.last_run_at,
            "failure_count": self.failure_count,
            "skipped_protected_object_count": self.skipped_protected_object_count,
            "audit_count": self.audit_count,
            "degraded": self.degraded,
            "degraded_reasons": list(self.degraded_reasons),
            "runner_status_counts": dict(self.runner_status_counts),
            "apply_status_counts": dict(self.apply_status_counts),
            "retention_capability": self.retention_capability,
            "live_scheduler_active": self.live_scheduler_active,
            "credential_loaded": self.credential_loaded,
            "mutation_triggered_by_status": self.mutation_triggered_by_status,
        }


def project_retention_status(
    *,
    enabled: bool,
    runner_records: Iterable[RetentionRunnerRecord] = (),
    apply_records: Iterable[RetentionApplyRecord] = (),
    next_run_at: str | None = None,
    status_policy_version: str = STATUS_POLICY_VERSION,
) -> RetentionStatusProjection:
    """Summarize retention records without invoking any retention behavior."""

    if not status_policy_version:
        raise ValueError("status_policy_version must be non-empty")
    runners = tuple(runner_records)
    applies = tuple(apply_records)

    runner_counts = Counter(record.status for record in runners)
    apply_counts = Counter(record.status for record in applies)
    timestamps = [
        timestamp
        for timestamp in (
            *(record.updated_at for record in runners),
            *(record.updated_at for record in applies),
        )
        if timestamp
    ]
    failure_count = (
        runner_counts.get("terminal_failure", 0)
        + apply_counts.get("partial_failure", 0)
        + sum(
            1
            for record in applies
            for entry in record.audit_entries
            if entry.action_status == "failed"
        )
    )
    skipped_protected_object_count = sum(
        _safe_int(record.audit_evidence.get("blocker_count", 0)) for record in runners
    )
    audit_count = sum(1 for record in runners if record.audit_evidence) + sum(
        len(record.audit_entries) for record in applies
    )
    degraded_reasons = _degraded_reasons(
        runner_counts=runner_counts,
        apply_counts=apply_counts,
        applies=applies,
    )

    return RetentionStatusProjection(
        enabled=enabled,
        status_policy_version=status_policy_version,
        next_run_at=next_run_at,
        last_run_at=max(timestamps) if timestamps else None,
        failure_count=failure_count,
        skipped_protected_object_count=skipped_protected_object_count,
        audit_count=audit_count,
        degraded=bool(degraded_reasons),
        degraded_reasons=degraded_reasons,
        runner_status_counts=dict(sorted(runner_counts.items())),
        apply_status_counts=dict(sorted(apply_counts.items())),
    )


def assert_no_secret_material(payload: Mapping[str, object]) -> None:
    """Fail closed if a public status payload contains obvious secret keys."""

    for key, value in _walk_payload(payload):
        lowered_key = key.lower()
        if any(marker in lowered_key for marker in _SECRET_MARKERS) and value not in (
            False,
            None,
            0,
            "",
        ):
            raise ValueError(f"public retention status contains secret-like key: {key}")
        if isinstance(value, str) and _looks_like_secret_value(value):
            raise ValueError("public retention status contains secret-like value")


def _degraded_reasons(
    *,
    runner_counts: Counter[str],
    apply_counts: Counter[str],
    applies: tuple[RetentionApplyRecord, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    for status in ("terminal_failure", "retrying", "lock_contended"):
        if runner_counts.get(status, 0):
            reasons.append(f"runner:{status}")
    if apply_counts.get("partial_failure", 0):
        reasons.append("apply:partial_failure")
    for record in applies:
        if record.safe_retry_required and record.problem_code:
            reasons.append(f"apply:{record.problem_code}")
    return tuple(dict.fromkeys(reasons))


def _safe_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _walk_payload(payload: Mapping[str, object], prefix: str = "") -> Iterable[tuple[str, object]]:
    for key, value in payload.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        yield path, value
        if isinstance(value, Mapping):
            yield from _walk_payload(value, path)
        elif isinstance(value, list | tuple):
            for index, item in enumerate(value):
                item_path = f"{path}[{index}]"
                yield item_path, item
                if isinstance(item, Mapping):
                    yield from _walk_payload(item, item_path)


def _looks_like_secret_value(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "ghp_",
            "github_pat_",
            "aws_secret_access_key",
            "begin private key",
            "sk-",
        )
    )


__all__ = [
    "STATUS_POLICY_VERSION",
    "RetentionStatusProjection",
    "assert_no_secret_material",
    "project_retention_status",
]
