"""Approval-bound retention apply controls (Story 130.4).

This module is package-local and adapter-injected. It never imports an object
storage SDK, never loads credentials, and never starts a scheduler. Callers must
provide an adapter that can verify manifest-backed object identity and perform
the requested transition/delete operation in tests or a future approved runtime
boundary.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final, Literal, Protocol

from replay.retention import RetentionDecision, RetentionDryRunPlan, RetentionObjectIdentity

APPLY_POLICY_VERSION: Final = "story130.retention.apply.v1"

RetentionApplyStatus = Literal["disabled", "blocked", "completed", "partial_failure"]
RetentionApplyActionStatus = Literal["skipped", "succeeded", "failed"]


@dataclass
class RetentionApplyError(Exception):
    """Fail-closed retention apply error with a stable code."""

    code: str
    message: str
    status_code: int = 409

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class RetentionApplyApprovalEvidence:
    """Operator approval bound to an exact retention dry-run plan hash."""

    plan_hash: str
    operator_identity: str
    approval_event_ref: str
    approved_at: str
    expires_at: str
    plan_generated_at: str


@dataclass(frozen=True)
class RetentionApplyConfig:
    """Configuration for one package-local retention apply invocation."""

    enabled: bool = False
    apply_policy_version: str = APPLY_POLICY_VERSION
    max_plan_age_seconds: int = 24 * 60 * 60

    def __post_init__(self) -> None:
        if not self.apply_policy_version:
            raise RetentionApplyError(
                "invalid_apply_policy_version", "apply_policy_version must be non-empty", 422
            )
        if self.max_plan_age_seconds <= 0:
            raise RetentionApplyError(
                "invalid_plan_age_limit", "max_plan_age_seconds must be > 0", 422
            )


@dataclass(frozen=True)
class RetentionApplyAuditEntry:
    """Per-object audit evidence for a retention apply decision."""

    plan_hash: str
    idempotency_key: str
    trace_id: str
    object_identity: Mapping[str, object]
    planned_action: str
    action_status: RetentionApplyActionStatus
    policy_basis: Mapping[str, object]
    adapter_response: Mapping[str, object]
    recovery_status: str
    recovery_evidence_ref: str | None
    operator_identity: str
    occurred_at: str
    failure_code: str | None = None
    failure_message: str | None = None


@dataclass(frozen=True)
class RetentionApplyRecord:
    """Persisted or returned retention apply result."""

    status: RetentionApplyStatus
    plan_hash: str
    dry_run_generated_at: str
    idempotency_key: str
    trace_id: str
    apply_policy_version: str
    operator_identity: str | None
    approval_event_ref: str | None
    created_at: str
    updated_at: str
    audit_entries: tuple[RetentionApplyAuditEntry, ...] = ()
    destructive_action_count: int = 0
    skipped_action_count: int = 0
    replayed: bool = False
    problem_code: str | None = None
    detail: str | None = None
    safe_retry_required: bool = False
    safe_retry_event_ref: str | None = None


class RetentionApplyAdapter(Protocol):
    """Injected object-lifecycle adapter boundary for Story 130.4."""

    def verify_object(self, identity: RetentionObjectIdentity) -> Mapping[str, object]:
        """Return metadata proving the current object still matches identity."""

    def transition_object(
        self,
        identity: RetentionObjectIdentity,
        *,
        idempotency_key: str,
        trace_id: str,
        recovery_evidence_ref: str,
    ) -> Mapping[str, object]:
        """Transition an object and return metadata-only adapter evidence."""

    def delete_object(
        self,
        identity: RetentionObjectIdentity,
        *,
        idempotency_key: str,
        trace_id: str,
        recovery_evidence_ref: str,
    ) -> Mapping[str, object]:
        """Delete an object and return metadata-only adapter evidence."""


class InMemoryRetentionApplyLedger:
    """Small in-memory Story 130.4 ledger for tests and package-local callers."""

    def __init__(self) -> None:
        self._records: dict[str, RetentionApplyRecord] = {}
        self._history: dict[str, list[RetentionApplyRecord]] = {}
        self._blocked_plans: set[str] = set()
        self._conflicts: dict[str, list[RetentionApplyRecord]] = {}

    def get(self, idempotency_key: str) -> RetentionApplyRecord | None:
        return self._records.get(idempotency_key)

    def put(self, record: RetentionApplyRecord) -> None:
        self._records[record.idempotency_key] = record
        self._history.setdefault(record.idempotency_key, []).append(record)
        if record.status == "partial_failure":
            self._blocked_plans.add(record.plan_hash)

    def put_conflict(self, idempotency_key: str, record: RetentionApplyRecord) -> None:
        self._conflicts.setdefault(idempotency_key, []).append(record)
        self._history.setdefault(idempotency_key, []).append(record)

    def successful_identity_keys(self, plan_hash: str) -> set[str]:
        keys: set[str] = set()
        for record in self._records.values():
            if record.plan_hash != plan_hash or record.status not in {
                "completed",
                "partial_failure",
            }:
                continue
            for entry in record.audit_entries:
                if entry.action_status == "succeeded" and entry.planned_action in {
                    "transition",
                    "delete",
                }:
                    identity = entry.object_identity
                    keys.add(
                        f"{identity['domain']}:{identity['object_key']}:{identity['version_or_generation']}"
                    )
        return keys

    def plan_blocked(self, plan_hash: str) -> bool:
        return plan_hash in self._blocked_plans

    def clear_plan_block(self, plan_hash: str) -> None:
        self._blocked_plans.discard(plan_hash)

    def history(self, idempotency_key: str) -> tuple[RetentionApplyRecord, ...]:
        return tuple(self._history.get(idempotency_key, ()))


def apply_retention_plan(
    *,
    config: RetentionApplyConfig,
    plan: RetentionDryRunPlan,
    approval: RetentionApplyApprovalEvidence,
    adapter: RetentionApplyAdapter,
    idempotency_key: str,
    recovery_evidence_refs: Mapping[str, str],
    ledger: InMemoryRetentionApplyLedger | None = None,
    trace_id: str | None = None,
    now: datetime | None = None,
    safe_retry_event_ref: str | None = None,
) -> RetentionApplyRecord:
    """Apply approved transition/delete decisions from a retention dry-run plan."""

    store = InMemoryRetentionApplyLedger() if ledger is None else ledger
    effective_now = _normalize_now(now)
    timestamp = _format_utc(effective_now)
    actual_trace_id = _trace_id(trace_id)
    _require_non_empty(idempotency_key, "idempotency_key", "idempotency_key_missing")

    base = RetentionApplyRecord(
        status="disabled",
        plan_hash=plan.plan_hash,
        dry_run_generated_at=plan.generated_at,
        idempotency_key=idempotency_key,
        trace_id=actual_trace_id,
        apply_policy_version=config.apply_policy_version,
        operator_identity=None,
        approval_event_ref=None,
        created_at=timestamp,
        updated_at=timestamp,
    )

    existing = store.get(idempotency_key)
    try:
        _validate_plan_integrity(plan)
    except RetentionApplyError as exc:
        blocked = replace(
            base,
            status="blocked",
            operator_identity=approval.operator_identity,
            approval_event_ref=approval.approval_event_ref,
            problem_code=exc.code,
            detail=exc.message,
        )
        if existing is None:
            store.put(blocked)
        else:
            store.put_conflict(idempotency_key, blocked)
        return blocked

    if existing is not None:
        if (
            existing.plan_hash == plan.plan_hash == approval.plan_hash
            and existing.dry_run_generated_at == plan.generated_at == approval.plan_generated_at
        ):
            return replace(existing, replayed=True)
        blocked = replace(
            base,
            status="blocked",
            operator_identity=approval.operator_identity,
            approval_event_ref=approval.approval_event_ref,
            problem_code="idempotency_plan_evidence_mismatch",
            detail=(
                "idempotency replay must match the submitted plan_hash, dry-run "
                "generated_at, approval plan_hash, and approval generated_at"
            ),
        )
        store.put_conflict(idempotency_key, blocked)
        return blocked

    if not config.enabled:
        return base

    if store.plan_blocked(plan.plan_hash) and not _has_text(safe_retry_event_ref):
        blocked = replace(
            base,
            status="blocked",
            problem_code="safe_retry_evidence_required",
            detail="previous partial failure blocks further destructive work for this plan",
            safe_retry_required=True,
            safe_retry_event_ref=safe_retry_event_ref,
        )
        store.put(blocked)
        return blocked
    try:
        _validate_approval(approval=approval, plan=plan, now=effective_now)
        _validate_plan_freshness(plan=plan, config=config, now=effective_now)
        if plan.blockers:
            raise RetentionApplyError(
                "dry_run_blockers_present",
                "retention apply requires blocker-free dry-run plan evidence",
                403,
            )
        _validate_recovery_evidence(plan.decisions, recovery_evidence_refs)
    except RetentionApplyError as exc:
        blocked = replace(
            base,
            status="blocked",
            operator_identity=approval.operator_identity,
            approval_event_ref=approval.approval_event_ref,
            problem_code=exc.code,
            detail=exc.message,
            safe_retry_event_ref=safe_retry_event_ref,
        )
        store.put(blocked)
        return blocked

    audit_entries: list[RetentionApplyAuditEntry] = []
    already_succeeded = (
        store.successful_identity_keys(plan.plan_hash)
        if _has_text(safe_retry_event_ref) and store.plan_blocked(plan.plan_hash)
        else set()
    )
    destructive_count = 0
    skipped_count = 0

    for decision in plan.decisions:
        identity = decision.object_identity
        if decision.planned_action not in {"transition", "delete"}:
            skipped_count += 1
            audit_entries.append(
                _audit_entry(
                    plan=plan,
                    decision=decision,
                    idempotency_key=idempotency_key,
                    trace_id=actual_trace_id,
                    approval=approval,
                    now=timestamp,
                    action_status="skipped",
                    adapter_response={"status": "skipped", "reason": decision.reason},
                    recovery_status="not_required",
                    recovery_evidence_ref=None,
                    safe_retry_event_ref=safe_retry_event_ref,
                )
            )
            continue

        recovery_ref = _recovery_ref(identity, recovery_evidence_refs)
        if _identity_key(identity) in already_succeeded:
            skipped_count += 1
            audit_entries.append(
                _audit_entry(
                    plan=plan,
                    decision=decision,
                    idempotency_key=idempotency_key,
                    trace_id=actual_trace_id,
                    approval=approval,
                    now=timestamp,
                    action_status="skipped",
                    adapter_response={
                        "status": "skipped",
                        "reason": "already_succeeded_in_prior_partial_failure",
                    },
                    recovery_status="already_recorded",
                    recovery_evidence_ref=recovery_ref,
                    safe_retry_event_ref=safe_retry_event_ref,
                )
            )
            continue
        try:
            verification = adapter.verify_object(identity)
            if verification.get("matched") is not True:
                raise RetentionApplyError(
                    "object_identity_mismatch",
                    "adapter verification did not match dry-run object identity",
                    409,
                )
            if decision.planned_action == "transition":
                response = adapter.transition_object(
                    identity,
                    idempotency_key=idempotency_key,
                    trace_id=actual_trace_id,
                    recovery_evidence_ref=recovery_ref,
                )
            else:
                response = adapter.delete_object(
                    identity,
                    idempotency_key=idempotency_key,
                    trace_id=actual_trace_id,
                    recovery_evidence_ref=recovery_ref,
                )
            if response.get("status") not in {"succeeded", "success", "ok"}:
                raise RetentionApplyError(
                    "adapter_action_failed",
                    "adapter response did not report a successful action",
                    409,
                )
            destructive_count += 1
            audit_entries.append(
                _audit_entry(
                    plan=plan,
                    decision=decision,
                    idempotency_key=idempotency_key,
                    trace_id=actual_trace_id,
                    approval=approval,
                    now=timestamp,
                    action_status="succeeded",
                    adapter_response={**dict(verification), **dict(response)},
                    recovery_status="recorded",
                    recovery_evidence_ref=recovery_ref,
                    safe_retry_event_ref=safe_retry_event_ref,
                )
            )
        except Exception as exc:  # noqa: BLE001 - adapter boundary is injected and fail-closed.
            error = (
                exc
                if isinstance(exc, RetentionApplyError)
                else RetentionApplyError(type(exc).__name__, str(exc), 409)
            )
            audit_entries.append(
                _audit_entry(
                    plan=plan,
                    decision=decision,
                    idempotency_key=idempotency_key,
                    trace_id=actual_trace_id,
                    approval=approval,
                    now=timestamp,
                    action_status="failed",
                    adapter_response={"status": "failed"},
                    recovery_status="review_required",
                    recovery_evidence_ref=recovery_ref,
                    failure_code=error.code,
                    failure_message=error.message,
                    safe_retry_event_ref=safe_retry_event_ref,
                )
            )
            record = replace(
                base,
                status="partial_failure",
                operator_identity=approval.operator_identity,
                approval_event_ref=approval.approval_event_ref,
                audit_entries=tuple(audit_entries),
                destructive_action_count=destructive_count,
                skipped_action_count=skipped_count,
                problem_code=error.code,
                detail=error.message,
                safe_retry_required=True,
                safe_retry_event_ref=safe_retry_event_ref,
            )
            store.put(record)
            return record

    record = replace(
        base,
        status="completed",
        operator_identity=approval.operator_identity,
        approval_event_ref=approval.approval_event_ref,
        audit_entries=tuple(audit_entries),
        destructive_action_count=destructive_count,
        skipped_action_count=skipped_count,
        safe_retry_event_ref=safe_retry_event_ref,
    )
    store.put(record)
    if _has_text(safe_retry_event_ref):
        store.clear_plan_block(plan.plan_hash)
    return record


def _validate_approval(
    *,
    approval: RetentionApplyApprovalEvidence,
    plan: RetentionDryRunPlan,
    now: datetime,
) -> None:
    if approval.plan_hash != plan.plan_hash:
        raise RetentionApplyError(
            "approval_plan_hash_mismatch",
            "approval evidence must match the exact retention dry-run plan_hash",
            403,
        )
    if approval.plan_generated_at != plan.generated_at:
        raise RetentionApplyError(
            "approval_plan_generated_at_mismatch",
            "approval evidence must match the exact retention dry-run generated_at evidence",
            403,
        )
    _require_non_empty(approval.operator_identity, "operator_identity", "approval_evidence_missing")
    _require_non_empty(
        approval.approval_event_ref, "approval_event_ref", "approval_evidence_missing"
    )
    approved_at = _parse_utc(approval.approved_at, "approved_at")
    expires_at = _parse_utc(approval.expires_at, "expires_at")
    if approved_at > now:
        raise RetentionApplyError("approval_from_future", "approval evidence is future dated", 403)
    if expires_at <= now:
        raise RetentionApplyError("approval_expired", "approval evidence has expired", 403)


def _validate_plan_integrity(plan: RetentionDryRunPlan) -> None:
    actual_hash = hashlib.sha256(plan.canonical_json().encode("utf-8")).hexdigest()
    if actual_hash != plan.plan_hash:
        raise RetentionApplyError(
            "dry_run_plan_hash_mismatch",
            "dry-run plan_hash must match the submitted plan canonical payload",
            403,
        )


def _validate_plan_freshness(
    *,
    plan: RetentionDryRunPlan,
    config: RetentionApplyConfig,
    now: datetime,
) -> None:
    generated_at = _parse_utc(plan.generated_at, "plan.generated_at")
    if generated_at > now:
        raise RetentionApplyError("plan_from_future", "dry-run plan is future dated", 403)
    age_seconds = (now - generated_at).total_seconds()
    if age_seconds > config.max_plan_age_seconds:
        raise RetentionApplyError("plan_expired", "dry-run plan evidence is stale", 403)


def _validate_recovery_evidence(
    decisions: tuple[RetentionDecision, ...],
    recovery_evidence_refs: Mapping[str, str],
) -> None:
    for decision in decisions:
        if decision.planned_action in {"transition", "delete"}:
            ref = _recovery_ref(decision.object_identity, recovery_evidence_refs)
            _require_non_empty(ref, "recovery_evidence_ref", "recovery_evidence_missing")


def _recovery_ref(
    identity: RetentionObjectIdentity,
    recovery_evidence_refs: Mapping[str, str],
) -> str:
    specific = _identity_key(identity)
    return recovery_evidence_refs.get(specific) or recovery_evidence_refs.get(
        identity.object_key, ""
    )


def _identity_key(identity: RetentionObjectIdentity) -> str:
    return f"{identity.domain}:{identity.object_key}:{identity.version_or_generation}"


def _audit_entry(
    *,
    plan: RetentionDryRunPlan,
    decision: RetentionDecision,
    idempotency_key: str,
    trace_id: str,
    approval: RetentionApplyApprovalEvidence,
    now: str,
    action_status: RetentionApplyActionStatus,
    adapter_response: Mapping[str, object],
    recovery_status: str,
    recovery_evidence_ref: str | None,
    failure_code: str | None = None,
    failure_message: str | None = None,
    safe_retry_event_ref: str | None = None,
) -> RetentionApplyAuditEntry:
    return RetentionApplyAuditEntry(
        plan_hash=plan.plan_hash,
        idempotency_key=idempotency_key,
        trace_id=trace_id,
        object_identity=_identity_payload(decision.object_identity),
        planned_action=decision.planned_action,
        action_status=action_status,
        policy_basis={
            "policy_id": plan.policy.policy_id,
            "policy_version": plan.policy.policy_version,
            "policy_domain": decision.policy_domain,
            "reason": decision.reason,
            "age_basis": decision.age_basis,
            "age_days": decision.age_days,
            "manifest_id": plan.manifest_id,
            "manifest_generated_at": plan.manifest_generated_at,
            "safe_retry_event_ref": safe_retry_event_ref,
        },
        adapter_response=dict(adapter_response),
        recovery_status=recovery_status,
        recovery_evidence_ref=recovery_evidence_ref,
        operator_identity=approval.operator_identity,
        occurred_at=now,
        failure_code=failure_code,
        failure_message=failure_message,
    )


def _identity_payload(identity: RetentionObjectIdentity) -> dict[str, object]:
    return {
        "domain": identity.domain,
        "manifest_ref": identity.manifest_ref,
        "object_key": identity.object_key,
        "version_or_generation": identity.version_or_generation,
        "etag_or_checksum": identity.etag_or_checksum,
        "size_bytes": identity.size_bytes,
        "created_at_utc": identity.created_at_utc,
        "last_modified_at_utc": identity.last_modified_at_utc,
        "storage_class": identity.storage_class,
        "hold_refs": list(identity.hold_refs),
    }


def _require_non_empty(value: str, field: str, code: str) -> None:
    if not _has_text(value):
        raise RetentionApplyError(code, f"{field} must be non-empty", 422)


def _has_text(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _trace_id(value: str | None) -> str:
    if value is None:
        return "retention-apply-local"
    if not value or any(char.isspace() for char in value):
        raise RetentionApplyError("invalid_trace_id", "trace_id must contain no whitespace", 422)
    return value


def _normalize_now(value: datetime | None) -> datetime:
    effective = datetime.now(UTC) if value is None else value
    if effective.tzinfo is None:
        raise RetentionApplyError("invalid_timestamp", "now must be timezone-aware UTC", 422)
    return effective.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: str, field: str) -> datetime:
    try:
        if not value.endswith("Z"):
            raise ValueError(value)
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise RetentionApplyError(
            "invalid_timestamp", f"{field} must be a strict UTC timestamp", 422
        ) from exc
    return parsed


__all__ = [
    "APPLY_POLICY_VERSION",
    "InMemoryRetentionApplyLedger",
    "RetentionApplyActionStatus",
    "RetentionApplyAdapter",
    "RetentionApplyApprovalEvidence",
    "RetentionApplyAuditEntry",
    "RetentionApplyConfig",
    "RetentionApplyError",
    "RetentionApplyRecord",
    "RetentionApplyStatus",
    "apply_retention_plan",
]
