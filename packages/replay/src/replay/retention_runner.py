"""Metadata-only scheduled retention job runner (Story 130.3).

The runner coordinates exactly one externally triggered scheduled retention slot
around the Story 130.2 dry-run planner. It is default-disabled, records only
status/audit metadata, never sleeps, never loads credentials, never calls object
storage, and never applies delete/transition/prune/archive-manifest mutations.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from replay.retention import (
    DEFAULT_POLICY_VERSION,
    RetentionDryRunPlan,
    RetentionPlanError,
    create_retention_dry_run_plan,
)

RUNNER_POLICY_VERSION: Final = "story130.retention.runner.v1"
TRACE_ID_PREFIX: Final = "retention-runner"

RetentionRunnerMode = Literal["dry_run", "apply"]
RetentionRunnerStatus = Literal[
    "disabled",
    "lock_contended",
    "started",
    "retrying",
    "completed",
    "terminal_failure",
    "apply_deferred",
]
PlannerCallable = Callable[..., RetentionDryRunPlan]


class RetentionRunnerConfigError(ValueError):
    """Retention runner configuration would violate Story 130.3 bounds."""


@dataclass(frozen=True)
class RetentionRunnerConfig:
    """Configuration for one package-local metadata-only runner invocation."""

    enabled: bool = False
    max_concurrency: int = 1
    max_attempts: int = 3
    base_backoff_seconds: int = 5
    max_backoff_seconds: int = 60
    runner_policy_version: str = RUNNER_POLICY_VERSION
    execution_fingerprint: str = "local-metadata-only"
    planner_identity: str = "create_retention_dry_run_plan"
    planner_version: str = DEFAULT_POLICY_VERSION

    def __post_init__(self) -> None:
        if self.max_concurrency != 1:
            raise RetentionRunnerConfigError(
                "Story 130.3 retention runner requires max_concurrency exactly 1"
            )
        if self.max_attempts < 1:
            raise RetentionRunnerConfigError("max_attempts must be >= 1")
        if self.base_backoff_seconds < 0:
            raise RetentionRunnerConfigError("base_backoff_seconds must be >= 0")
        if self.max_backoff_seconds < self.base_backoff_seconds:
            raise RetentionRunnerConfigError("max_backoff_seconds must be >= base_backoff_seconds")
        for name, value in (
            ("runner_policy_version", self.runner_policy_version),
            ("execution_fingerprint", self.execution_fingerprint),
            ("planner_identity", self.planner_identity),
            ("planner_version", self.planner_version),
        ):
            if value == "":
                raise RetentionRunnerConfigError(f"{name} must be non-empty")


@dataclass(frozen=True)
class RetentionRunnerRequest:
    """Externally triggered schedule-slot input for Story 130.3."""

    schedule_slot: str
    mode: RetentionRunnerMode
    policy_path: Path
    object_manifest_path: Path
    pre_run_input_reference: str
    policy_input_reference: str
    manifest_input_reference: str
    trace_id: str | None = None
    now: datetime | None = None

    def __post_init__(self) -> None:
        if self.schedule_slot == "":
            raise RetentionRunnerConfigError("schedule_slot must be non-empty")
        if self.mode not in {"dry_run", "apply"}:
            raise RetentionRunnerConfigError("mode must be dry_run or apply")
        if self.pre_run_input_reference == "":
            raise RetentionRunnerConfigError("pre_run_input_reference must be non-empty")
        if self.policy_input_reference == "":
            raise RetentionRunnerConfigError("policy_input_reference must be non-empty")
        if self.manifest_input_reference == "":
            raise RetentionRunnerConfigError("manifest_input_reference must be non-empty")


@dataclass(frozen=True)
class RetentionRetryMetadata:
    """Fakeable retry/backoff metadata. No real sleep is performed."""

    max_attempts: int
    retryable: bool
    next_attempt: int | None
    backoff_seconds: int
    sleep_performed: bool = False


@dataclass(frozen=True)
class RetentionLockMetadata:
    """Metadata-only lock outcome for a runner invocation."""

    acquired: bool
    released: bool
    lock_key: str
    reason: str


@dataclass(frozen=True)
class RetentionRunnerRecord:
    """Persisted or returned runner status/audit metadata."""

    status: RetentionRunnerStatus
    idempotency_key: str
    schedule_slot: str
    mode: RetentionRunnerMode
    trace_id: str
    attempt: int
    runner_policy_version: str
    execution_fingerprint: str
    planner_identity: str
    planner_version: str
    pre_run_input_reference: str
    policy_input_reference: str
    manifest_input_reference: str
    policy_path: str
    manifest_path: str
    retry: RetentionRetryMetadata
    lock: RetentionLockMetadata
    created_at: str
    updated_at: str
    audit_evidence: Mapping[str, object] = field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None

    @property
    def is_active(self) -> bool:
        """Return whether the record represents active work."""
        return self.status in {"started", "retrying"}


class InMemoryRetentionRunnerLedger:
    """Small in-memory metadata ledger for tests and package-local callers."""

    def __init__(self) -> None:
        self._records: dict[str, RetentionRunnerRecord] = {}
        self._locks: set[str] = set()
        self._history: dict[str, list[RetentionRunnerRecord]] = {}

    def get(self, idempotency_key: str) -> RetentionRunnerRecord | None:
        return self._records.get(idempotency_key)

    def put(self, record: RetentionRunnerRecord) -> None:
        self._records[record.idempotency_key] = record
        self._history.setdefault(record.idempotency_key, []).append(record)

    def acquire_lock(self, idempotency_key: str) -> bool:
        if idempotency_key in self._locks:
            return False
        self._locks.add(idempotency_key)
        return True

    def release_lock(self, idempotency_key: str) -> None:
        self._locks.discard(idempotency_key)

    def lock_present(self, idempotency_key: str) -> bool:
        return idempotency_key in self._locks

    def history(self, idempotency_key: str) -> tuple[RetentionRunnerRecord, ...]:
        return tuple(self._history.get(idempotency_key, ()))


def compute_retention_runner_idempotency_key(
    *,
    config: RetentionRunnerConfig,
    request: RetentionRunnerRequest,
) -> str:
    """Compute the deterministic pre-run Story 130.3 coordination key."""
    payload = {
        "schedule_slot": request.schedule_slot,
        "runner_policy_version": config.runner_policy_version,
        "execution_fingerprint": config.execution_fingerprint,
        "planner_identity": config.planner_identity,
        "planner_version": config.planner_version,
        "mode": request.mode,
        "pre_run_input_reference": request.pre_run_input_reference,
        "policy_input_reference": request.policy_input_reference,
        "manifest_input_reference": request.manifest_input_reference,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_scheduled_retention_job(
    *,
    config: RetentionRunnerConfig,
    request: RetentionRunnerRequest,
    ledger: InMemoryRetentionRunnerLedger | None = None,
    planner: PlannerCallable = create_retention_dry_run_plan,
) -> RetentionRunnerRecord:
    """Run one externally triggered metadata-only retention schedule slot."""
    store = InMemoryRetentionRunnerLedger() if ledger is None else ledger
    idempotency_key = compute_retention_runner_idempotency_key(config=config, request=request)
    trace_id = _trace_id(request.trace_id)
    now = _format_now(request.now)
    base = _base_record(
        config=config,
        request=request,
        idempotency_key=idempotency_key,
        trace_id=trace_id,
        now=now,
    )

    if not config.enabled:
        return replace(base, status="disabled")

    existing = store.get(idempotency_key)
    if existing is not None:
        replay = _handle_existing_record(existing=existing, config=config, store=store, now=now)
        if replay is not None:
            return replay
        next_attempt = existing.retry.next_attempt or existing.attempt + 1
    else:
        next_attempt = 1

    if not store.acquire_lock(idempotency_key):
        return _lock_contended(base, now=now)

    started = replace(
        base,
        status="started",
        attempt=next_attempt,
        updated_at=now,
        lock=RetentionLockMetadata(
            acquired=True,
            released=False,
            lock_key=idempotency_key,
            reason="acquired",
        ),
        retry=_retry_metadata(config, attempt=next_attempt, retryable=False),
    )
    store.put(started)
    try:
        if request.mode == "apply":
            final = replace(
                started,
                status="apply_deferred",
                updated_at=now,
                lock=RetentionLockMetadata(
                    acquired=True,
                    released=True,
                    lock_key=idempotency_key,
                    reason="released_after_apply_deferred",
                ),
                retry=_retry_metadata(config, attempt=next_attempt, retryable=False),
                audit_evidence={
                    "mode": "apply",
                    "apply_deferred": True,
                    "mutation_performed": False,
                    "storage_call_performed": False,
                    "credential_loaded": False,
                },
            )
            store.put(final)
            return final
        plan = planner(
            policy_path=request.policy_path,
            object_manifest_path=request.object_manifest_path,
            now=request.now,
            safety_policy_version=config.planner_version,
        )
        final = replace(
            started,
            status="completed",
            updated_at=now,
            lock=RetentionLockMetadata(
                acquired=True,
                released=True,
                lock_key=idempotency_key,
                reason="released_after_completed",
            ),
            retry=_retry_metadata(config, attempt=next_attempt, retryable=False),
            audit_evidence=_plan_evidence(plan),
        )
        store.put(final)
        return final
    except RetentionPlanError as exc:
        final = _terminal_failure(started, now=now, error=exc)
        store.put(final)
        return final
    except Exception as exc:  # noqa: BLE001 - retry classification is runner metadata.
        if next_attempt >= config.max_attempts:
            final = _terminal_failure(started, now=now, error=exc)
        else:
            final = replace(
                started,
                status="retrying",
                updated_at=now,
                lock=RetentionLockMetadata(
                    acquired=True,
                    released=True,
                    lock_key=idempotency_key,
                    reason="released_after_retryable_failure",
                ),
                retry=_retry_metadata(config, attempt=next_attempt, retryable=True),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        store.put(final)
        return final
    finally:
        store.release_lock(idempotency_key)


def _handle_existing_record(
    *,
    existing: RetentionRunnerRecord,
    config: RetentionRunnerConfig,
    store: InMemoryRetentionRunnerLedger,
    now: str,
) -> RetentionRunnerRecord | None:
    if existing.status in {"completed", "terminal_failure", "apply_deferred"}:
        return existing
    if store.lock_present(existing.idempotency_key):
        return _lock_contended(existing, now=now)
    if existing.status == "started":
        if existing.attempt >= config.max_attempts:
            terminal = _terminal_failure(
                existing,
                now=now,
                error=RetentionRunnerConfigError("stale started record exhausted retry limit"),
            )
            store.put(terminal)
            return terminal
        retrying = replace(
            existing,
            status="retrying",
            updated_at=now,
            lock=RetentionLockMetadata(
                acquired=False,
                released=False,
                lock_key=existing.idempotency_key,
                reason="stale_started_without_lock",
            ),
            retry=_retry_metadata(config, attempt=existing.attempt, retryable=True),
        )
        store.put(retrying)
        return retrying
    if existing.status == "retrying":
        if existing.retry.next_attempt is None or existing.retry.next_attempt > config.max_attempts:
            terminal = _terminal_failure(
                existing,
                now=now,
                error=RetentionRunnerConfigError("retry limit exhausted"),
            )
            store.put(terminal)
            return terminal
        return None
    return None


def _base_record(
    *,
    config: RetentionRunnerConfig,
    request: RetentionRunnerRequest,
    idempotency_key: str,
    trace_id: str,
    now: str,
) -> RetentionRunnerRecord:
    return RetentionRunnerRecord(
        status="disabled",
        idempotency_key=idempotency_key,
        schedule_slot=request.schedule_slot,
        mode=request.mode,
        trace_id=trace_id,
        attempt=0,
        runner_policy_version=config.runner_policy_version,
        execution_fingerprint=config.execution_fingerprint,
        planner_identity=config.planner_identity,
        planner_version=config.planner_version,
        pre_run_input_reference=request.pre_run_input_reference,
        policy_input_reference=request.policy_input_reference,
        manifest_input_reference=request.manifest_input_reference,
        policy_path=str(request.policy_path),
        manifest_path=str(request.object_manifest_path),
        retry=_retry_metadata(config, attempt=0, retryable=False),
        lock=RetentionLockMetadata(
            acquired=False,
            released=False,
            lock_key=idempotency_key,
            reason="not_attempted",
        ),
        created_at=now,
        updated_at=now,
    )


def _lock_contended(record: RetentionRunnerRecord, *, now: str) -> RetentionRunnerRecord:
    return replace(
        record,
        status="lock_contended",
        updated_at=now,
        lock=RetentionLockMetadata(
            acquired=False,
            released=False,
            lock_key=record.idempotency_key,
            reason="lock_present",
        ),
    )


def _terminal_failure(
    record: RetentionRunnerRecord,
    *,
    now: str,
    error: Exception,
) -> RetentionRunnerRecord:
    return replace(
        record,
        status="terminal_failure",
        updated_at=now,
        lock=RetentionLockMetadata(
            acquired=record.lock.acquired,
            released=True,
            lock_key=record.idempotency_key,
            reason="released_after_terminal_failure",
        ),
        retry=replace(record.retry, retryable=False, next_attempt=None, backoff_seconds=0),
        error_type=type(error).__name__,
        error_message=str(error),
    )


def _retry_metadata(
    config: RetentionRunnerConfig,
    *,
    attempt: int,
    retryable: bool,
) -> RetentionRetryMetadata:
    next_attempt = attempt + 1 if retryable and attempt < config.max_attempts else None
    backoff = 0
    if next_attempt is not None:
        exponent = max(attempt - 1, 0)
        backoff = min(config.base_backoff_seconds * (2**exponent), config.max_backoff_seconds)
    return RetentionRetryMetadata(
        max_attempts=config.max_attempts,
        retryable=retryable,
        next_attempt=next_attempt,
        backoff_seconds=backoff,
    )


def _plan_evidence(plan: RetentionDryRunPlan) -> dict[str, object]:
    action_counts = Counter(decision.planned_action for decision in plan.decisions)
    return {
        "plan_hash": plan.plan_hash,
        "artifact_filename": plan.artifact_filename,
        "plan_generated_at": plan.generated_at,
        "policy_id": plan.policy.policy_id,
        "policy_version": plan.policy.policy_version,
        "manifest_id": plan.manifest_id,
        "manifest_generated_at": plan.manifest_generated_at,
        "action_counts": dict(sorted(action_counts.items())),
        "blocker_count": len(plan.blockers),
        "mutation_performed": False,
        "storage_call_performed": False,
        "credential_loaded": False,
    }


def _trace_id(value: str | None) -> str:
    if value is None:
        return f"{TRACE_ID_PREFIX}-{uuid.uuid4()}"
    if value == "" or any(char.isspace() for char in value):
        raise RetentionRunnerConfigError("trace_id must be non-empty and contain no whitespace")
    return value


def _format_now(value: datetime | None) -> str:
    effective = datetime.now(UTC) if value is None else value
    if effective.tzinfo is None:
        raise RetentionRunnerConfigError("now must be timezone-aware UTC")
    return effective.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "InMemoryRetentionRunnerLedger",
    "RUNNER_POLICY_VERSION",
    "RetentionLockMetadata",
    "RetentionRetryMetadata",
    "RetentionRunnerConfig",
    "RetentionRunnerConfigError",
    "RetentionRunnerMode",
    "RetentionRunnerRecord",
    "RetentionRunnerRequest",
    "RetentionRunnerStatus",
    "compute_retention_runner_idempotency_key",
    "run_scheduled_retention_job",
]
