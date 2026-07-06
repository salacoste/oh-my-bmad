"""Tests for Story 130.4 approval-bound retention apply."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from replay.retention import (
    RetentionDryRunPlan,
    RetentionObjectIdentity,
    create_retention_dry_run_plan,
)
from replay.retention_apply import (
    InMemoryRetentionApplyLedger,
    RetentionApplyApprovalEvidence,
    RetentionApplyConfig,
    RetentionApplyError,
    RetentionApplyRecord,
    apply_retention_plan,
)

_NOW = datetime(2026, 7, 6, 10, 0, 0, tzinfo=UTC)


def _policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy_id": "retention-policy-test",
        "policy_version": "2026-07-06.1",
        "owner": "data-owner@example.invalid",
        "authority_source": "Story 130.4 test policy",
        "generated_at": "2026-07-05T10:00:00Z",
        "evidence_max_age_days": 7,
        "default_action": "retain",
        "domains": {
            "event-archive": {
                "retention_window_days": 30,
                "allowed_actions": ["retain", "transition", "delete"],
                "transition_after_days": 60,
                "delete_after_days": 90,
                "protected_holds": ["legal-hold"],
                "excluded_keys": ["archive/protected.jsonl"],
            }
        },
    }


def _obj(key: str, last_modified: str, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "domain": "event-archive",
        "manifest_ref": "manifest-1",
        "object_key": key,
        "version_or_generation": f"v-{key}",
        "etag_or_checksum": f"sha256-{key}",
        "size_bytes": 123,
        "created_at_utc": "2026-01-01T00:00:00Z",
        "last_modified_at_utc": last_modified,
        "storage_class": "standard",
        "hold_refs": [],
    }
    data.update(overrides)
    return data


def _manifest(*, blockers: bool = False) -> dict[str, object]:
    objects = [
        _obj("archive/recent.jsonl", "2026-06-20T10:00:00Z"),
        _obj("archive/transition.jsonl", "2026-05-01T10:00:00Z"),
        _obj("archive/delete.jsonl", "2026-03-01T10:00:00Z"),
    ]
    if blockers:
        objects.extend(
            [
                _obj("archive/hold.jsonl", "2026-03-01T10:00:00Z", hold_refs=["legal-hold"]),
                _obj("archive/protected.jsonl", "2026-03-01T10:00:00Z"),
            ]
        )
    return {
        "schema_version": 1,
        "manifest_id": "manifest-1",
        "generated_at": "2026-07-05T10:00:00Z",
        "objects": objects,
    }


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")


def _plan(tmp_path: Path, *, blockers: bool = False) -> RetentionDryRunPlan:
    policy_path = tmp_path / "policy.json"
    manifest_path = tmp_path / "manifest.json"
    _write_json(policy_path, _policy())
    _write_json(manifest_path, _manifest(blockers=blockers))
    return create_retention_dry_run_plan(
        policy_path=policy_path,
        object_manifest_path=manifest_path,
        now=_NOW,
    )


def _approval(plan: RetentionDryRunPlan, **overrides: str) -> RetentionApplyApprovalEvidence:
    data = {
        "plan_hash": plan.plan_hash,
        "operator_identity": "operator@example.invalid",
        "approval_event_ref": "approval:event:130.4",
        "approved_at": "2026-07-06T09:55:00Z",
        "expires_at": "2026-07-06T11:00:00Z",
        "plan_generated_at": plan.generated_at,
    }
    data.update(overrides)
    return RetentionApplyApprovalEvidence(**data)


def _recovery_refs() -> dict[str, str]:
    return {
        "archive/transition.jsonl": "recovery:transition:archive/transition.jsonl",
        "archive/delete.jsonl": "recovery:delete:archive/delete.jsonl",
    }


class FakeApplyAdapter:
    def __init__(self, *, fail_on: str | None = None, mismatch_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.mismatch_on = mismatch_on
        self.calls: list[tuple[str, str, str | None]] = []

    def verify_object(self, identity: RetentionObjectIdentity) -> dict[str, object]:
        self.calls.append(("verify", identity.object_key, None))
        return {
            "status": "verified",
            "matched": identity.object_key != self.mismatch_on,
            "etag_or_checksum": identity.etag_or_checksum,
            "version_or_generation": identity.version_or_generation,
        }

    def transition_object(
        self,
        identity: RetentionObjectIdentity,
        *,
        idempotency_key: str,
        trace_id: str,
        recovery_evidence_ref: str,
    ) -> dict[str, object]:
        self.calls.append(("transition", identity.object_key, recovery_evidence_ref))
        if identity.object_key == self.fail_on:
            raise RuntimeError("transition failed")
        return {
            "status": "succeeded",
            "adapter_action": "transition",
            "target_storage_class": "cold",
            "idempotency_key": idempotency_key,
            "trace_id": trace_id,
        }

    def delete_object(
        self,
        identity: RetentionObjectIdentity,
        *,
        idempotency_key: str,
        trace_id: str,
        recovery_evidence_ref: str,
    ) -> dict[str, object]:
        self.calls.append(("delete", identity.object_key, recovery_evidence_ref))
        if identity.object_key == self.fail_on:
            raise RuntimeError("delete failed")
        return {
            "status": "succeeded",
            "adapter_action": "delete",
            "restore_evidence_ref": recovery_evidence_ref,
            "idempotency_key": idempotency_key,
            "trace_id": trace_id,
        }


def _apply(
    plan: RetentionDryRunPlan,
    adapter: FakeApplyAdapter,
    *,
    approval: RetentionApplyApprovalEvidence | None = None,
    ledger: InMemoryRetentionApplyLedger | None = None,
    idempotency_key: str = "apply-key-1",
    recovery_refs: dict[str, str] | None = None,
    safe_retry_event_ref: str | None = None,
) -> RetentionApplyRecord:
    return apply_retention_plan(
        config=RetentionApplyConfig(enabled=True),
        plan=plan,
        approval=_approval(plan) if approval is None else approval,
        adapter=adapter,
        idempotency_key=idempotency_key,
        recovery_evidence_refs=_recovery_refs() if recovery_refs is None else recovery_refs,
        ledger=ledger,
        trace_id="trace-130-4",
        now=_NOW,
        safe_retry_event_ref=safe_retry_event_ref,
    )


def test_disabled_apply_returns_metadata_without_adapter_calls(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    adapter = FakeApplyAdapter()

    record = apply_retention_plan(
        config=RetentionApplyConfig(),
        plan=plan,
        approval=_approval(plan),
        adapter=adapter,
        idempotency_key="apply-key-disabled",
        recovery_evidence_refs=_recovery_refs(),
        trace_id="trace-130-4",
        now=_NOW,
    )

    assert record.status == "disabled"
    assert adapter.calls == []


def test_missing_or_mismatched_approval_fails_closed_without_adapter_calls(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    adapter = FakeApplyAdapter()

    mismatch = _approval(plan, plan_hash="different-plan-hash")
    record = _apply(plan, adapter, approval=mismatch)

    assert record.status == "blocked"
    assert record.problem_code == "approval_plan_hash_mismatch"
    assert adapter.calls == []

    empty_operator = _approval(plan, operator_identity="")
    record = _apply(plan, adapter, approval=empty_operator, idempotency_key="apply-key-2")

    assert record.status == "blocked"
    assert record.problem_code == "approval_evidence_missing"
    assert adapter.calls == []


def test_expired_approval_or_stale_plan_fails_closed(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    adapter = FakeApplyAdapter()

    expired = _approval(plan, expires_at="2026-07-06T09:59:59Z")
    record = _apply(plan, adapter, approval=expired)

    assert record.status == "blocked"
    assert record.problem_code == "approval_expired"

    record = apply_retention_plan(
        config=RetentionApplyConfig(enabled=True, max_plan_age_seconds=1),
        plan=plan,
        approval=_approval(plan),
        adapter=adapter,
        idempotency_key="apply-key-stale",
        recovery_evidence_refs=_recovery_refs(),
        trace_id="trace-130-4",
        now=_NOW.replace(hour=10, minute=0, second=2),
    )

    assert record.status == "blocked"
    assert record.problem_code == "plan_expired"
    assert adapter.calls == []


def test_approval_generated_at_mismatch_fails_closed_without_adapter_calls(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    approval = _approval(plan)
    restamped_plan = replace(plan, generated_at="2026-07-06T09:59:00Z")
    adapter = FakeApplyAdapter()

    record = _apply(restamped_plan, adapter, approval=approval)

    assert record.status == "blocked"
    assert record.problem_code == "approval_plan_generated_at_mismatch"
    assert adapter.calls == []


def test_tampered_decision_with_preserved_plan_hash_fails_closed(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    tampered_decision = replace(
        plan.decisions[0],
        planned_action="delete",
        reason="tampered delete",
    )
    tampered_plan = replace(
        plan,
        decisions=(tampered_decision, *plan.decisions[1:]),
    )
    adapter = FakeApplyAdapter()

    record = _apply(tampered_plan, adapter, approval=_approval(tampered_plan))

    assert record.status == "blocked"
    assert record.problem_code == "dry_run_plan_hash_mismatch"
    assert adapter.calls == []


def test_tampered_removed_blockers_with_preserved_plan_hash_fails_closed(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path, blockers=True)
    tampered_plan = replace(plan, blockers=())
    adapter = FakeApplyAdapter()

    record = _apply(tampered_plan, adapter, approval=_approval(tampered_plan))

    assert record.status == "blocked"
    assert record.problem_code == "dry_run_plan_hash_mismatch"
    assert adapter.calls == []


def test_tampered_object_identity_with_preserved_plan_hash_fails_closed(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    tampered_identity = replace(
        plan.decisions[1].object_identity,
        object_key="archive/tampered-transition.jsonl",
        version_or_generation="v-archive/tampered-transition.jsonl",
        etag_or_checksum="sha256-archive/tampered-transition.jsonl",
    )
    tampered_decision = replace(plan.decisions[1], object_identity=tampered_identity)
    tampered_plan = replace(
        plan,
        decisions=(plan.decisions[0], tampered_decision, *plan.decisions[2:]),
    )
    adapter = FakeApplyAdapter()

    record = _apply(tampered_plan, adapter, approval=_approval(tampered_plan))

    assert record.status == "blocked"
    assert record.problem_code == "dry_run_plan_hash_mismatch"
    assert adapter.calls == []


def test_plan_with_blockers_fails_closed_before_mutation(tmp_path: Path) -> None:
    plan = _plan(tmp_path, blockers=True)
    adapter = FakeApplyAdapter()

    record = _apply(plan, adapter)

    assert record.status == "blocked"
    assert record.problem_code == "dry_run_blockers_present"
    assert adapter.calls == []


def test_missing_recovery_evidence_fails_closed_before_mutation(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    adapter = FakeApplyAdapter()

    record = _apply(
        plan, adapter, recovery_refs={"archive/transition.jsonl": "recovery:transition"}
    )

    assert record.status == "blocked"
    assert record.problem_code == "recovery_evidence_missing"
    assert adapter.calls == []


def test_apply_verifies_identity_then_records_transition_delete_audit(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    adapter = FakeApplyAdapter()
    ledger = InMemoryRetentionApplyLedger()

    record = _apply(plan, adapter, ledger=ledger)

    assert record.status == "completed"
    assert record.destructive_action_count == 2
    assert record.skipped_action_count == 1
    assert [call[0] for call in adapter.calls] == ["verify", "transition", "verify", "delete"]
    assert [entry.planned_action for entry in record.audit_entries] == [
        "retain",
        "transition",
        "delete",
    ]
    transition = record.audit_entries[1]
    assert transition.action_status == "succeeded"
    assert transition.object_identity["object_key"] == "archive/transition.jsonl"
    assert transition.policy_basis["policy_id"] == "retention-policy-test"
    assert transition.adapter_response["matched"] is True
    assert transition.adapter_response["adapter_action"] == "transition"
    assert transition.recovery_status == "recorded"
    assert transition.recovery_evidence_ref == "recovery:transition:archive/transition.jsonl"
    delete = record.audit_entries[2]
    assert delete.adapter_response["adapter_action"] == "delete"
    assert delete.trace_id == "trace-130-4"
    assert delete.operator_identity == "operator@example.invalid"
    assert ledger.history("apply-key-1") == (record,)


def test_completed_apply_replays_without_duplicate_adapter_calls(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    adapter = FakeApplyAdapter()
    ledger = InMemoryRetentionApplyLedger()

    first = _apply(plan, adapter, ledger=ledger)
    replay = _apply(plan, adapter, ledger=ledger)

    assert first.status == "completed"
    assert replay.status == "completed"
    assert replay.replayed is True
    assert [call[0] for call in adapter.calls] == ["verify", "transition", "verify", "delete"]


def test_identity_mismatch_blocks_mutation_and_requires_safe_retry(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    adapter = FakeApplyAdapter(mismatch_on="archive/transition.jsonl")
    ledger = InMemoryRetentionApplyLedger()

    first = _apply(plan, adapter, ledger=ledger)

    assert first.status == "partial_failure"
    assert first.problem_code == "object_identity_mismatch"
    assert first.safe_retry_required is True
    assert [call[0] for call in adapter.calls] == ["verify"]
    assert first.audit_entries[-1].action_status == "failed"
    assert first.audit_entries[-1].recovery_status == "review_required"

    blocked = _apply(
        plan,
        FakeApplyAdapter(),
        ledger=ledger,
        idempotency_key="apply-key-after-partial",
    )

    assert blocked.status == "blocked"
    assert blocked.problem_code == "safe_retry_evidence_required"
    assert blocked.safe_retry_required is True


def test_safe_retry_evidence_allows_new_attempt_after_partial_failure(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    ledger = InMemoryRetentionApplyLedger()
    first_adapter = FakeApplyAdapter(fail_on="archive/delete.jsonl")

    first = _apply(plan, first_adapter, ledger=ledger)
    assert first.status == "partial_failure"
    assert first.destructive_action_count == 1

    retry_adapter = FakeApplyAdapter()
    retry = _apply(
        plan,
        retry_adapter,
        ledger=ledger,
        idempotency_key="apply-key-safe-retry",
        safe_retry_event_ref="review:safe-retry-approved",
    )

    assert retry.status == "completed"
    assert retry.safe_retry_event_ref == "review:safe-retry-approved"
    assert [call[0] for call in retry_adapter.calls] == ["verify", "delete"]
    assert retry_adapter.calls == [
        ("verify", "archive/delete.jsonl", None),
        ("delete", "archive/delete.jsonl", "recovery:delete:archive/delete.jsonl"),
    ]
    skipped_transition = retry.audit_entries[1]
    assert skipped_transition.planned_action == "transition"
    assert skipped_transition.action_status == "skipped"
    assert skipped_transition.recovery_status == "already_recorded"
    assert (
        skipped_transition.adapter_response["reason"]
        == "already_succeeded_in_prior_partial_failure"
    )
    assert all(
        entry.policy_basis["safe_retry_event_ref"] == "review:safe-retry-approved"
        for entry in retry.audit_entries
    )


def test_safe_retry_with_regenerated_same_hash_plan_skips_prior_success(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    ledger = InMemoryRetentionApplyLedger()
    first_adapter = FakeApplyAdapter(fail_on="archive/delete.jsonl")

    first = _apply(plan, first_adapter, ledger=ledger)
    regenerated_plan = replace(plan, generated_at="2026-07-06T09:59:00Z")
    regenerated_approval = _approval(regenerated_plan)
    retry_adapter = FakeApplyAdapter()

    retry = _apply(
        regenerated_plan,
        retry_adapter,
        ledger=ledger,
        approval=regenerated_approval,
        idempotency_key="apply-key-safe-retry-regenerated",
        safe_retry_event_ref="review:safe-retry-approved-regenerated",
    )

    assert first.status == "partial_failure"
    assert first.plan_hash == regenerated_plan.plan_hash
    assert first.dry_run_generated_at != regenerated_plan.generated_at
    assert retry.status == "completed"
    assert [call[0] for call in retry_adapter.calls] == ["verify", "delete"]
    skipped_transition = retry.audit_entries[1]
    assert skipped_transition.planned_action == "transition"
    assert skipped_transition.action_status == "skipped"
    assert (
        skipped_transition.adapter_response["reason"]
        == "already_succeeded_in_prior_partial_failure"
    )


def test_idempotency_replay_requires_same_submitted_plan_hash(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    adapter = FakeApplyAdapter()
    ledger = InMemoryRetentionApplyLedger()

    first = _apply(plan, adapter, ledger=ledger, idempotency_key="shared-key")
    restamped_plan = replace(plan, generated_at="2026-07-06T09:59:00Z")
    restamped_approval = _approval(restamped_plan)
    restamped_replay = _apply(
        restamped_plan,
        adapter,
        ledger=ledger,
        approval=restamped_approval,
        idempotency_key="shared-key",
    )
    changed_plan = replace(plan, plan_hash="different-plan-hash")
    changed_approval = _approval(changed_plan)
    replay = _apply(
        changed_plan,
        adapter,
        ledger=ledger,
        approval=changed_approval,
        idempotency_key="shared-key",
    )

    assert first.status == "completed"
    assert first.dry_run_generated_at == plan.generated_at
    assert restamped_replay.status == "blocked"
    assert restamped_replay.problem_code == "idempotency_plan_evidence_mismatch"
    assert restamped_replay.replayed is False
    assert replay.status == "blocked"
    assert replay.problem_code == "dry_run_plan_hash_mismatch"
    assert replay.plan_hash == "different-plan-hash"
    assert replay.replayed is False

    original_replay = _apply(plan, adapter, ledger=ledger, idempotency_key="shared-key")

    assert original_replay.status == "completed"
    assert original_replay.plan_hash == plan.plan_hash
    assert original_replay.replayed is True
    assert [call[0] for call in adapter.calls] == ["verify", "transition", "verify", "delete"]


def test_invalid_safe_retry_attempt_does_not_clear_partial_failure_block(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    ledger = InMemoryRetentionApplyLedger()
    first = _apply(plan, FakeApplyAdapter(fail_on="archive/delete.jsonl"), ledger=ledger)
    assert first.status == "partial_failure"
    assert ledger.plan_blocked(plan.plan_hash) is True

    invalid_retry = _apply(
        plan,
        FakeApplyAdapter(),
        ledger=ledger,
        idempotency_key="invalid-safe-retry",
        recovery_refs={"archive/transition.jsonl": "recovery:transition"},
        safe_retry_event_ref="review:safe-retry-approved",
    )

    assert invalid_retry.status == "blocked"
    assert invalid_retry.problem_code == "recovery_evidence_missing"
    assert ledger.plan_blocked(plan.plan_hash) is True


def test_no_evidence_retry_after_invalid_safe_retry_remains_blocked_without_adapter_calls(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    ledger = InMemoryRetentionApplyLedger()
    first = _apply(plan, FakeApplyAdapter(fail_on="archive/delete.jsonl"), ledger=ledger)
    assert first.status == "partial_failure"

    invalid_retry_adapter = FakeApplyAdapter()
    invalid_retry = _apply(
        plan,
        invalid_retry_adapter,
        ledger=ledger,
        idempotency_key="invalid-safe-retry",
        recovery_refs={"archive/transition.jsonl": "recovery:transition"},
        safe_retry_event_ref="review:safe-retry-approved",
    )
    assert invalid_retry.status == "blocked"
    assert invalid_retry_adapter.calls == []

    no_evidence_adapter = FakeApplyAdapter()
    blocked = _apply(
        plan,
        no_evidence_adapter,
        ledger=ledger,
        idempotency_key="after-invalid-safe-retry",
    )

    assert blocked.status == "blocked"
    assert blocked.problem_code == "safe_retry_evidence_required"
    assert no_evidence_adapter.calls == []


def test_missing_idempotency_key_raises_before_adapter_calls(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    adapter = FakeApplyAdapter()

    with pytest.raises(RetentionApplyError, match="idempotency_key"):
        _apply(plan, adapter, idempotency_key="")

    assert adapter.calls == []


def test_exported_config_is_available_from_package_root() -> None:
    from replay import RetentionApplyConfig as ExportedRetentionApplyConfig

    assert ExportedRetentionApplyConfig is RetentionApplyConfig
