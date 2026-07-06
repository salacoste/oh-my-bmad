"""Tests for Story 130.3 metadata-only scheduled retention runner."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from replay import RetentionRunnerConfig as ExportedRetentionRunnerConfig
from replay.retention import RetentionDryRunPlan, create_retention_dry_run_plan
from replay.retention_runner import (
    InMemoryRetentionRunnerLedger,
    RetentionRunnerConfig,
    RetentionRunnerConfigError,
    RetentionRunnerRequest,
    compute_retention_runner_idempotency_key,
    run_scheduled_retention_job,
)

_NOW = datetime(2026, 7, 6, 10, 0, 0, tzinfo=UTC)


def _policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy_id": "retention-policy-test",
        "policy_version": "2026-07-06.1",
        "owner": "data-owner@example.invalid",
        "authority_source": "Story 130.3 test policy",
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


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "manifest_id": "manifest-1",
        "generated_at": "2026-07-05T10:00:00Z",
        "objects": [
            _obj("archive/recent.jsonl", "2026-06-20T10:00:00Z"),
            _obj("archive/transition.jsonl", "2026-05-01T10:00:00Z"),
            _obj("archive/delete.jsonl", "2026-03-01T10:00:00Z"),
            _obj("archive/hold.jsonl", "2026-03-01T10:00:00Z", hold_refs=["legal-hold"]),
            _obj("archive/protected.jsonl", "2026-03-01T10:00:00Z"),
        ],
    }


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    policy_path = tmp_path / "policy.json"
    manifest_path = tmp_path / "manifest.json"
    _write_json(policy_path, _policy())
    _write_json(manifest_path, _manifest())
    return policy_path, manifest_path


def _request(
    tmp_path: Path,
    *,
    mode: str = "dry_run",
    trace_id: str | None = "trace-130-3",
    schedule_slot: str = "2026-07-06T10:00:00Z/PT1H",
) -> RetentionRunnerRequest:
    policy_path, manifest_path = _write_inputs(tmp_path)
    assert mode in {"dry_run", "apply"}
    return RetentionRunnerRequest(
        schedule_slot=schedule_slot,
        mode=mode,  # type: ignore[arg-type]
        policy_path=policy_path,
        object_manifest_path=manifest_path,
        pre_run_input_reference="runner-input-ref-1",
        policy_input_reference="policy-ref-1",
        manifest_input_reference="manifest-ref-1",
        trace_id=trace_id,
        now=_NOW,
    )


def _enabled(
    *,
    max_attempts: int = 3,
    base_backoff_seconds: int = 5,
    max_backoff_seconds: int = 60,
    execution_fingerprint: str = "local-metadata-only",
) -> RetentionRunnerConfig:
    return RetentionRunnerConfig(
        enabled=True,
        max_attempts=max_attempts,
        base_backoff_seconds=base_backoff_seconds,
        max_backoff_seconds=max_backoff_seconds,
        execution_fingerprint=execution_fingerprint,
    )


def test_default_disabled_returns_metadata_without_planner_or_active_record(tmp_path: Path) -> None:
    calls = 0

    def planner(**_: object) -> RetentionDryRunPlan:
        nonlocal calls
        calls += 1
        raise AssertionError("planner must not be called when runner is disabled")

    ledger = InMemoryRetentionRunnerLedger()
    request = _request(tmp_path)
    record = run_scheduled_retention_job(
        config=RetentionRunnerConfig(), request=request, ledger=ledger, planner=planner
    )

    assert record.status == "disabled"
    assert calls == 0
    assert ledger.get(record.idempotency_key) is None
    assert record.lock.acquired is False


def test_enabled_dry_run_invokes_planner_records_completed_and_post_run_evidence(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    before = (request.policy_path.read_bytes(), request.object_manifest_path.read_bytes())
    ledger = InMemoryRetentionRunnerLedger()

    record = run_scheduled_retention_job(config=_enabled(), request=request, ledger=ledger)

    assert record.status == "completed"
    assert record.trace_id == "trace-130-3"
    assert record.attempt == 1
    assert record.lock.acquired is True
    assert record.lock.released is True
    assert record.retry.sleep_performed is False
    assert record.audit_evidence["policy_id"] == "retention-policy-test"
    assert record.audit_evidence["policy_version"] == "2026-07-06.1"
    assert record.audit_evidence["manifest_id"] == "manifest-1"
    assert record.audit_evidence["manifest_generated_at"] == "2026-07-05T10:00:00Z"
    assert record.audit_evidence["artifact_filename"] == (
        f"retention-dry-run-plan-{record.audit_evidence['plan_hash']}.json"
    )
    assert record.audit_evidence["action_counts"] == {
        "blocked": 2,
        "delete": 1,
        "retain": 1,
        "transition": 1,
    }
    assert record.audit_evidence["blocker_count"] == 2
    assert record.audit_evidence["mutation_performed"] is False
    assert (request.policy_path.read_bytes(), request.object_manifest_path.read_bytes()) == before
    assert [entry.status for entry in ledger.history(record.idempotency_key)] == [
        "started",
        "completed",
    ]
    assert ledger.lock_present(record.idempotency_key) is False


def test_single_lock_contention_returns_lock_contended_without_planner(tmp_path: Path) -> None:
    request = _request(tmp_path)
    config = _enabled()
    ledger = InMemoryRetentionRunnerLedger()
    key = compute_retention_runner_idempotency_key(config=config, request=request)
    assert ledger.acquire_lock(key) is True

    def planner(**_: object) -> RetentionDryRunPlan:
        raise AssertionError("planner must not run while lock is present")

    record = run_scheduled_retention_job(
        config=config, request=request, ledger=ledger, planner=planner
    )

    assert record.status == "lock_contended"
    assert record.lock.reason == "lock_present"
    assert ledger.history(key) == ()


def test_config_rejects_any_concurrency_above_or_below_one() -> None:
    with pytest.raises(RetentionRunnerConfigError, match="max_concurrency exactly 1"):
        RetentionRunnerConfig(enabled=True, max_concurrency=2)
    with pytest.raises(RetentionRunnerConfigError, match="max_concurrency exactly 1"):
        RetentionRunnerConfig(enabled=True, max_concurrency=0)


def test_idempotency_key_is_pre_run_and_excludes_trace_retry_and_plan_evidence(
    tmp_path: Path,
) -> None:
    config = _enabled(max_attempts=3, base_backoff_seconds=5)
    request = _request(tmp_path, trace_id="trace-a")
    key = compute_retention_runner_idempotency_key(config=config, request=request)

    different_trace = replace(request, trace_id="trace-b")
    different_retry = replace(config, max_attempts=9, base_backoff_seconds=30)
    assert compute_retention_runner_idempotency_key(config=config, request=different_trace) == key
    assert compute_retention_runner_idempotency_key(config=different_retry, request=request) == key

    policy = _policy()
    policy["policy_id"] = "changed-policy-id"
    policy["policy_version"] = "changed-policy-version"
    _write_json(request.policy_path, policy)
    manifest = _manifest()
    manifest["manifest_id"] = "changed-manifest-id"
    manifest["generated_at"] = "2026-07-06T09:00:00Z"
    _write_json(request.object_manifest_path, manifest)
    assert compute_retention_runner_idempotency_key(config=config, request=request) == key

    assert (
        compute_retention_runner_idempotency_key(
            config=config,
            request=replace(request, schedule_slot="2026-07-06T11:00:00Z/PT1H"),
        )
        != key
    )
    assert (
        compute_retention_runner_idempotency_key(
            config=config, request=replace(request, mode="apply")
        )
        != key
    )
    assert (
        compute_retention_runner_idempotency_key(
            config=config,
            request=replace(request, pre_run_input_reference="runner-input-ref-2"),
        )
        != key
    )
    assert (
        compute_retention_runner_idempotency_key(
            config=replace(config, execution_fingerprint="different-runner"), request=request
        )
        != key
    )


def test_idempotency_key_uses_references_not_local_paths(tmp_path: Path) -> None:
    config = _enabled()
    request = _request(tmp_path)
    key = compute_retention_runner_idempotency_key(config=config, request=request)
    other_policy, other_manifest = _write_inputs(tmp_path / "other")
    same_refs_different_paths = replace(
        request,
        policy_path=other_policy,
        object_manifest_path=other_manifest,
    )

    assert (
        compute_retention_runner_idempotency_key(
            config=config,
            request=same_refs_different_paths,
        )
        == key
    )


def test_request_rejects_invalid_mode_at_runtime(tmp_path: Path) -> None:
    policy_path, manifest_path = _write_inputs(tmp_path)
    with pytest.raises(RetentionRunnerConfigError, match="mode must be dry_run or apply"):
        RetentionRunnerRequest(
            schedule_slot="2026-07-06T10:00:00Z/PT1H",
            mode="invalid",  # type: ignore[arg-type]
            policy_path=policy_path,
            object_manifest_path=manifest_path,
            pre_run_input_reference="runner-input-ref-1",
            policy_input_reference="policy-ref-1",
            manifest_input_reference="manifest-ref-1",
            now=_NOW,
        )


def test_trace_id_is_preserved_or_generated(tmp_path: Path) -> None:
    explicit = run_scheduled_retention_job(
        config=RetentionRunnerConfig(), request=_request(tmp_path, trace_id="trace-explicit")
    )
    generated = run_scheduled_retention_job(
        config=RetentionRunnerConfig(), request=_request(tmp_path, trace_id=None)
    )

    assert explicit.trace_id == "trace-explicit"
    assert generated.trace_id.startswith("retention-runner-")
    uuid.UUID(generated.trace_id.removeprefix("retention-runner-"))


def test_retrying_record_reenters_next_attempt_without_sleep(tmp_path: Path) -> None:
    request = _request(tmp_path)
    ledger = InMemoryRetentionRunnerLedger()
    config = _enabled(max_attempts=2, base_backoff_seconds=7, max_backoff_seconds=7)
    calls = 0

    def flaky_planner(**kwargs: object) -> RetentionDryRunPlan:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary planner failure")
        return create_retention_dry_run_plan(**kwargs)  # type: ignore[arg-type]

    first = run_scheduled_retention_job(
        config=config, request=request, ledger=ledger, planner=flaky_planner
    )
    second = run_scheduled_retention_job(
        config=config, request=request, ledger=ledger, planner=flaky_planner
    )

    assert first.status == "retrying"
    assert first.retry.retryable is True
    assert first.retry.next_attempt == 2
    assert first.retry.backoff_seconds == 7
    assert first.retry.sleep_performed is False
    assert second.status == "completed"
    assert second.attempt == 2
    assert calls == 2
    assert [entry.status for entry in ledger.history(second.idempotency_key)] == [
        "started",
        "retrying",
        "started",
        "completed",
    ]


def test_terminal_failure_is_distinct_from_retryable_failure(tmp_path: Path) -> None:
    def always_fails(**_: object) -> RetentionDryRunPlan:
        raise RuntimeError("boom")

    record = run_scheduled_retention_job(
        config=_enabled(max_attempts=1),
        request=_request(tmp_path),
        ledger=InMemoryRetentionRunnerLedger(),
        planner=always_fails,
    )

    assert record.status == "terminal_failure"
    assert record.retry.retryable is False
    assert record.retry.next_attempt is None
    assert record.error_type == "RuntimeError"


def test_completed_replay_returns_persisted_metadata_without_planner(tmp_path: Path) -> None:
    request = _request(tmp_path)
    ledger = InMemoryRetentionRunnerLedger()
    first = run_scheduled_retention_job(config=_enabled(), request=request, ledger=ledger)

    def planner(**_: object) -> RetentionDryRunPlan:
        raise AssertionError("completed replay must not call planner")

    second = run_scheduled_retention_job(
        config=_enabled(), request=request, ledger=ledger, planner=planner
    )

    assert second == first
    assert len(ledger.history(first.idempotency_key)) == 2


def test_started_with_present_lock_replays_as_lock_contended(tmp_path: Path) -> None:
    request = _request(tmp_path)
    ledger = InMemoryRetentionRunnerLedger()

    def fails(**_: object) -> RetentionDryRunPlan:
        raise RuntimeError("temporary")

    retrying = run_scheduled_retention_job(
        config=_enabled(max_attempts=2), request=request, ledger=ledger, planner=fails
    )
    started = ledger.history(retrying.idempotency_key)[0]
    isolated = InMemoryRetentionRunnerLedger()
    isolated.put(started)
    assert isolated.acquire_lock(started.idempotency_key) is True

    replay = run_scheduled_retention_job(
        config=_enabled(max_attempts=2), request=request, ledger=isolated, planner=fails
    )

    assert replay.status == "lock_contended"
    assert replay.lock.reason == "lock_present"


def test_stale_started_without_lock_follows_retry_or_terminal_policy(tmp_path: Path) -> None:
    request = _request(tmp_path)

    def fails(**_: object) -> RetentionDryRunPlan:
        raise RuntimeError("temporary")

    source = InMemoryRetentionRunnerLedger()
    retrying = run_scheduled_retention_job(
        config=_enabled(max_attempts=2), request=request, ledger=source, planner=fails
    )
    started = source.history(retrying.idempotency_key)[0]

    retry_store = InMemoryRetentionRunnerLedger()
    retry_store.put(started)
    retry_replay = run_scheduled_retention_job(
        config=_enabled(max_attempts=2), request=request, ledger=retry_store, planner=fails
    )
    assert retry_replay.status == "retrying"
    assert retry_replay.retry.next_attempt == 2

    terminal_store = InMemoryRetentionRunnerLedger()
    terminal_store.put(started)
    terminal_replay = run_scheduled_retention_job(
        config=_enabled(max_attempts=1), request=request, ledger=terminal_store, planner=fails
    )
    assert terminal_replay.status == "terminal_failure"


def test_apply_mode_records_deferred_intent_only_and_replays_without_planner(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path, mode="apply")
    before = (request.policy_path.read_bytes(), request.object_manifest_path.read_bytes())
    ledger = InMemoryRetentionRunnerLedger()
    calls = 0

    def planner(**_: object) -> RetentionDryRunPlan:
        nonlocal calls
        calls += 1
        raise AssertionError("apply is deferred and must not call planner or storage")

    first = run_scheduled_retention_job(
        config=_enabled(), request=request, ledger=ledger, planner=planner
    )
    second = run_scheduled_retention_job(
        config=_enabled(), request=request, ledger=ledger, planner=planner
    )

    assert first.status == "apply_deferred"
    assert second == first
    assert calls == 0
    assert first.audit_evidence == {
        "mode": "apply",
        "apply_deferred": True,
        "mutation_performed": False,
        "storage_call_performed": False,
        "credential_loaded": False,
    }
    assert (request.policy_path.read_bytes(), request.object_manifest_path.read_bytes()) == before
    assert [entry.status for entry in ledger.history(first.idempotency_key)] == [
        "started",
        "apply_deferred",
    ]


def test_invalid_policy_is_terminal_failure_not_retrying(tmp_path: Path) -> None:
    request = _request(tmp_path)
    policy = _policy()
    policy["default_action"] = "delete"
    _write_json(request.policy_path, policy)

    record = run_scheduled_retention_job(
        config=_enabled(max_attempts=3), request=request, ledger=InMemoryRetentionRunnerLedger()
    )

    assert record.status == "terminal_failure"
    assert record.error_type == "RetentionPlanError"
    assert record.retry.next_attempt is None


def test_public_api_exports_runner_config() -> None:
    assert ExportedRetentionRunnerConfig is RetentionRunnerConfig
