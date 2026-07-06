"""Tests for Story 130.2 retention dry-run manifest validation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from replay.retention import RetentionDryRunPlan, RetentionPlanError, create_retention_dry_run_plan

_NOW = datetime(2026, 7, 6, 10, 0, 0, tzinfo=UTC)


def _policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "policy_id": "retention-policy-test",
        "policy_version": "2026-07-06.1",
        "owner": "data-owner@example.invalid",
        "authority_source": "Story 130.2 test policy",
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
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")


def _write_inputs(
    tmp_path: Path,
    *,
    policy: dict[str, object] | None = None,
    manifest: dict[str, object] | None = None,
) -> tuple[Path, Path]:
    policy_path = tmp_path / "policy.json"
    manifest_path = tmp_path / "manifest.json"
    _write_json(policy_path, _policy() if policy is None else policy)
    _write_json(manifest_path, _manifest() if manifest is None else manifest)
    return policy_path, manifest_path


def _create(
    tmp_path: Path,
    *,
    policy: dict[str, object] | None = None,
    manifest: dict[str, object] | None = None,
) -> RetentionDryRunPlan:
    policy_path, manifest_path = _write_inputs(tmp_path, policy=policy, manifest=manifest)
    before = (policy_path.read_bytes(), manifest_path.read_bytes())
    plan = create_retention_dry_run_plan(
        policy_path=policy_path,
        object_manifest_path=manifest_path,
        now=_NOW,
    )
    assert (policy_path.read_bytes(), manifest_path.read_bytes()) == before
    return plan


def test_valid_manifest_returns_deterministic_metadata_only_plan(tmp_path: Path) -> None:
    plan = _create(tmp_path)
    assert [d.planned_action for d in plan.decisions] == [
        "retain",
        "transition",
        "delete",
        "blocked",
        "blocked",
    ]
    assert [b.code for b in plan.blockers] == ["protected_hold_active", "operator_exclusion_active"]
    assert all(d.age_basis == "last_modified_at_utc" for d in plan.decisions)
    assert plan.artifact_filename == f"retention-dry-run-plan-{plan.plan_hash}.json"

    again = _create(tmp_path)
    assert again.plan_hash == plan.plan_hash
    assert again.canonical_json() == plan.canonical_json()


def test_missing_checksum_or_version_fails_closed(tmp_path: Path) -> None:
    manifest = _manifest()
    objects = manifest["objects"]
    assert isinstance(objects, list)
    del objects[0]["etag_or_checksum"]
    with pytest.raises(RetentionPlanError, match="etag_or_checksum"):
        _create(tmp_path, manifest=manifest)

    manifest = _manifest()
    objects = manifest["objects"]
    assert isinstance(objects, list)
    del objects[0]["version_or_generation"]
    with pytest.raises(RetentionPlanError, match="version_or_generation"):
        _create(tmp_path, manifest=manifest)


@pytest.mark.parametrize(
    "bad_key",
    [
        "",
        "/absolute",
        "../escape",
        "archive/prefix/",
        "archive/*.jsonl",
        "archive/?.jsonl",
        "archive\\bad",
    ],
)
def test_invalid_object_keys_fail_closed(tmp_path: Path, bad_key: str) -> None:
    manifest = _manifest()
    objects = manifest["objects"]
    assert isinstance(objects, list)
    objects[0]["object_key"] = bad_key
    with pytest.raises(
        RetentionPlanError, match="object_key|POSIX|prefix|wildcard|relative|non-empty"
    ):
        _create(tmp_path, manifest=manifest)


def test_repeated_same_domain_object_key_fails_even_with_different_version_and_checksum(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    manifest["objects"] = [
        _obj(
            "archive/repeated.jsonl",
            "2026-03-01T10:00:00Z",
            version_or_generation="v1",
            etag_or_checksum="sha256-a",
        ),
        _obj(
            "archive/repeated.jsonl",
            "2026-03-02T10:00:00Z",
            version_or_generation="v2",
            etag_or_checksum="sha256-b",
        ),
    ]
    with pytest.raises(RetentionPlanError, match="repeats object key"):
        _create(tmp_path, manifest=manifest)


def test_unknown_domain_fails_closed(tmp_path: Path) -> None:
    manifest = _manifest()
    objects = manifest["objects"]
    assert isinstance(objects, list)
    objects[0]["domain"] = "unknown-domain"
    with pytest.raises(RetentionPlanError, match="not declared"):
        _create(tmp_path, manifest=manifest)


def test_allowed_actions_required_per_domain(tmp_path: Path) -> None:
    policy = _policy()
    domains = policy["domains"]
    assert isinstance(domains, dict)
    domain_policy = domains["event-archive"]
    assert isinstance(domain_policy, dict)
    del domain_policy["allowed_actions"]
    with pytest.raises(RetentionPlanError, match="allowed_actions"):
        _create(tmp_path, policy=policy)


def test_policy_action_conflicts_fail_closed(tmp_path: Path) -> None:
    policy = _policy()
    domains = policy["domains"]
    assert isinstance(domains, dict)
    domain_policy = domains["event-archive"]
    assert isinstance(domain_policy, dict)
    domain_policy["allowed_actions"] = ["retain", "transition"]
    with pytest.raises(RetentionPlanError, match="delete_after_days requires delete"):
        _create(tmp_path, policy=policy)

    policy = _policy()
    policy["default_action"] = "delete"
    with pytest.raises(RetentionPlanError, match="default_action"):
        _create(tmp_path, policy=policy)


def test_invalid_interval_order_fails_closed(tmp_path: Path) -> None:
    policy = _policy()
    domains = policy["domains"]
    assert isinstance(domains, dict)
    domain_policy = domains["event-archive"]
    assert isinstance(domain_policy, dict)
    domain_policy["delete_after_days"] = 50
    with pytest.raises(
        RetentionPlanError, match="delete_after_days must be >= transition_after_days"
    ):
        _create(tmp_path, policy=policy)


def test_excluded_key_prefix_misuse_fails_closed(tmp_path: Path) -> None:
    policy = _policy()
    domains = policy["domains"]
    assert isinstance(domains, dict)
    domain_policy = domains["event-archive"]
    assert isinstance(domain_policy, dict)
    domain_policy["excluded_keys"] = ["archive/prefix/"]
    with pytest.raises(RetentionPlanError, match="exact object key"):
        _create(tmp_path, policy=policy)


def test_future_and_stale_timestamps_fail_closed(tmp_path: Path) -> None:
    manifest = _manifest()
    objects = manifest["objects"]
    assert isinstance(objects, list)
    objects[0]["last_modified_at_utc"] = "2026-07-07T10:00:00Z"
    with pytest.raises(RetentionPlanError, match="future"):
        _create(tmp_path, manifest=manifest)

    manifest = _manifest()
    manifest["generated_at"] = "2026-06-01T10:00:00Z"
    with pytest.raises(RetentionPlanError, match="older than evidence_max_age_days"):
        _create(tmp_path, manifest=manifest)


def test_created_after_last_modified_fails_closed(tmp_path: Path) -> None:
    manifest = _manifest()
    objects = manifest["objects"]
    assert isinstance(objects, list)
    objects[0]["created_at_utc"] = "2026-06-21T10:00:00Z"
    with pytest.raises(RetentionPlanError, match="created_at_utc"):
        _create(tmp_path, manifest=manifest)
