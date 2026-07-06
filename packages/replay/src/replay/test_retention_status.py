"""Tests for Story 130.5 read-only retention observability."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from replay.retention_apply import InMemoryRetentionApplyLedger
from replay.retention_runner import InMemoryRetentionRunnerLedger, run_scheduled_retention_job
from replay.retention_status import (
    STATUS_POLICY_VERSION,
    assert_no_secret_material,
    project_retention_status,
)
from replay.test_retention_apply import FakeApplyAdapter
from replay.test_retention_apply import _apply as apply_plan
from replay.test_retention_apply import _plan as apply_plan_fixture
from replay.test_retention_runner import _enabled, _request

_NOW = datetime(2026, 7, 6, 10, 0, 0, tzinfo=UTC)


def test_status_projection_reports_empty_disabled_state_without_side_effects() -> None:
    status = project_retention_status(enabled=False)
    payload = status.to_public_dict()

    assert payload["enabled"] is False
    assert payload["status_policy_version"] == STATUS_POLICY_VERSION
    assert payload["last_run_at"] is None
    assert payload["next_run_at"] is None
    assert payload["failure_count"] == 0
    assert payload["audit_count"] == 0
    assert payload["live_scheduler_active"] is False
    assert payload["credential_loaded"] is False
    assert payload["mutation_triggered_by_status"] is False


def test_status_projection_summarizes_runner_apply_and_next_run(tmp_path: Path) -> None:
    runner_ledger = InMemoryRetentionRunnerLedger()
    runner_record = run_scheduled_retention_job(
        config=_enabled(),
        request=_request(tmp_path, schedule_slot="2026-07-06T10:00:00Z/PT1H"),
        ledger=runner_ledger,
    )
    plan = apply_plan_fixture(tmp_path)
    apply_ledger = InMemoryRetentionApplyLedger()
    apply_record = apply_plan(plan, FakeApplyAdapter(), ledger=apply_ledger)

    status = project_retention_status(
        enabled=True,
        runner_records=(runner_record,),
        apply_records=(apply_record,),
        next_run_at="2026-07-06T11:00:00Z",
    )
    payload = status.to_public_dict()

    assert payload["enabled"] is True
    assert payload["next_run_at"] == "2026-07-06T11:00:00Z"
    assert payload["last_run_at"] == "2026-07-06T10:00:00Z"
    assert payload["runner_status_counts"] == {"completed": 1}
    assert payload["apply_status_counts"] == {"completed": 1}
    assert payload["skipped_protected_object_count"] == 2
    assert payload["audit_count"] == 4
    assert payload["failure_count"] == 0
    assert payload["degraded"] is False


def test_status_projection_reports_degraded_partial_failure_without_retrying(
    tmp_path: Path,
) -> None:
    plan = apply_plan_fixture(tmp_path)
    ledger = InMemoryRetentionApplyLedger()
    partial = apply_plan(plan, FakeApplyAdapter(fail_on="archive/delete.jsonl"), ledger=ledger)
    blocked = apply_plan(
        plan,
        FakeApplyAdapter(),
        ledger=ledger,
        idempotency_key="after-partial",
    )

    status = project_retention_status(enabled=True, apply_records=(partial, blocked))

    assert status.degraded is True
    assert "apply:partial_failure" in status.degraded_reasons
    assert "apply:safe_retry_evidence_required" in status.degraded_reasons
    assert status.failure_count == 2
    assert status.audit_count == 3


def test_status_projection_is_read_only_and_does_not_call_planner_or_adapter(
    tmp_path: Path,
) -> None:
    runner_record = run_scheduled_retention_job(
        config=_enabled(),
        request=_request(tmp_path),
        ledger=InMemoryRetentionRunnerLedger(),
    )
    poison_runner = replace(runner_record, updated_at="2026-07-06T10:05:00Z")

    status = project_retention_status(
        enabled=True,
        runner_records=(poison_runner,),
        apply_records=(),
        next_run_at="2026-07-06T11:00:00Z",
    )

    assert status.last_run_at == "2026-07-06T10:05:00Z"
    assert status.mutation_triggered_by_status is False
    assert status.live_scheduler_active is False


def test_public_status_payload_omits_paths_identities_and_secret_material(tmp_path: Path) -> None:
    runner_record = run_scheduled_retention_job(
        config=_enabled(execution_fingerprint="local-metadata-only"),
        request=_request(tmp_path),
        ledger=InMemoryRetentionRunnerLedger(),
    )
    status = project_retention_status(enabled=True, runner_records=(runner_record,))
    payload = status.to_public_dict()

    rendered = repr(payload)
    assert str(tmp_path) not in rendered
    assert "operator@example.invalid" not in rendered
    assert "policy-ref-1" not in rendered
    assert "manifest-ref-1" not in rendered
    assert_no_secret_material(payload)

    with pytest.raises(ValueError, match="secret-like"):
        assert_no_secret_material({"access_token": "github_pat_should_not_render"})


def test_status_policy_version_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="status_policy_version"):
        project_retention_status(enabled=True, status_policy_version="")
