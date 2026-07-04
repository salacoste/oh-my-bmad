"""Tests for Phase 14 lifecycle dry-run planning."""

from __future__ import annotations

import hashlib
import inspect
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from events import Actor, EventEnvelope, to_canonical_json

import replay
from replay.archive_manifest import collect_hot_segments
from replay.errors import ReplayArchiveManifestError
from replay.lifecycle import (
    LifecycleArchiveCoverage,
    LifecycleBlocker,
    LifecycleDecision,
    LifecycleDryRunPlan,
    LifecycleRetentionPolicy,
    LifecycleSegmentIdentity,
    create_lifecycle_dry_run_plan,
)

_ACTOR = Actor(kind="system", id="pytest")
_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000000"
_REQUEST_ID = "01917e5c-a7d1-7000-8abc-000000000001"
_NOW = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)


def _event(*, mono_ns: int, event_id_suffix: int = 1) -> EventEnvelope:
    suffix = f"{event_id_suffix:012x}"
    return EventEnvelope(
        event_id=f"e-01917e5c-a7d1-7000-8abc-{suffix}",
        schema_version="1.1.0",
        type="task.created",
        emitted_at=_NOW,
        emitted_at_monotonic_ns=mono_ns,
        actor=_ACTOR,
        payload={"task_id": f"t-{suffix}", "title": "Lifecycle test"},
        trace_id=_TRACE_ID,
        request_id=_REQUEST_ID,
    )


def _write_jsonl(path: Path, envelopes: list[EventEnvelope]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        for env in envelopes:
            f.write(to_canonical_json(env) + b"\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(
    manifest_dir: Path,
    *,
    segment_file: Path,
    logical_date: str,
    original_relpath: str | None = None,
) -> Path:
    manifest_dir.mkdir(parents=True, exist_ok=True)
    envelopes = list(collect_hot_segments(segment_file.parent)[0].envelopes)
    manifest = {
        "schema_version": 1,
        "manifest_id": "m-test",
        "created_at": "2026-06-11T00:00:00Z",
        "created_by": "pytest",
        "segments": [
            {
                "logical_date": logical_date,
                "original_relpath": original_relpath or segment_file.name,
                "archive_relpath": segment_file.name,
                "sha256": _sha256(segment_file),
                "event_count": len(envelopes),
                "first_sequence": min(env.emitted_at_monotonic_ns for env in envelopes),
                "last_sequence": max(env.emitted_at_monotonic_ns for env in envelopes),
                "archived_at": "2026-06-11T00:00:00Z",
                "actor_id": "pytest",
            }
        ],
    }
    manifest_path = manifest_dir / "lifecycle-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _hot_segment(tmp_path: Path, name: str, *, mono_ns: int) -> Path:
    path = tmp_path / "hot" / name
    _write_jsonl(path, [_event(mono_ns=mono_ns, event_id_suffix=mono_ns)])
    return path


def _archived_copy(tmp_path: Path, hot_path: Path) -> tuple[Path, Path]:
    archive_file = tmp_path / "archive" / hot_path.name
    archive_file.parent.mkdir()
    archive_file.write_bytes(hot_path.read_bytes())
    manifest = _write_manifest(
        archive_file.parent,
        segment_file=archive_file,
        logical_date=hot_path.stem,
        original_relpath=hot_path.name,
    )
    return archive_file, manifest


def test_dry_run_plan_hash_is_stable_for_same_inputs(tmp_path: Path) -> None:
    hot_path = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    _, manifest = _archived_copy(tmp_path, hot_path)

    first = create_lifecycle_dry_run_plan(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )
    second = create_lifecycle_dry_run_plan(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )

    assert first.plan_hash == second.plan_hash
    assert first.canonical_json() == second.canonical_json()


def test_dry_run_plan_hash_changes_when_retention_policy_changes(tmp_path: Path) -> None:
    _hot_segment(tmp_path, "2026-06-05.jsonl", mono_ns=5)

    retained = create_lifecycle_dry_run_plan(
        event_log_dir=tmp_path / "hot",
        retain_hot_days=7,
        now=_NOW,
    )
    blocked = create_lifecycle_dry_run_plan(
        event_log_dir=tmp_path / "hot",
        retain_hot_days=1,
        now=_NOW,
    )

    assert retained.plan_hash != blocked.plan_hash
    assert retained.decisions[0].status == "retained"
    assert blocked.decisions[0].status == "blocked"


def test_archived_old_hot_segment_is_eligible(tmp_path: Path) -> None:
    hot_path = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    _, manifest = _archived_copy(tmp_path, hot_path)

    plan = create_lifecycle_dry_run_plan(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )

    assert plan.decisions[0].status == "eligible"
    assert plan.decisions[0].archive_coverage.matched is True
    assert plan.blockers == ()


def test_unarchived_old_hot_segment_is_blocked_not_eligible(tmp_path: Path) -> None:
    _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)

    plan = create_lifecycle_dry_run_plan(
        event_log_dir=tmp_path / "hot",
        retain_hot_days=7,
        now=_NOW,
    )

    assert plan.decisions[0].status == "blocked"
    assert plan.blockers[0].code == "archive_coverage_missing"


def test_recent_hot_segment_is_retained(tmp_path: Path) -> None:
    _hot_segment(tmp_path, "2026-06-10.jsonl", mono_ns=10)

    plan = create_lifecycle_dry_run_plan(
        event_log_dir=tmp_path / "hot",
        retain_hot_days=7,
        now=_NOW,
    )

    assert plan.decisions[0].status == "retained"
    assert plan.decisions[0].reason == "within_retention_window"


def test_invalid_archive_manifest_fails_closed(tmp_path: Path) -> None:
    _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    manifest = tmp_path / "bad.json"
    manifest.write_text("{}", encoding="utf-8")

    with pytest.raises(ReplayArchiveManifestError):
        create_lifecycle_dry_run_plan(
            event_log_dir=tmp_path / "hot",
            archive_manifest_path=manifest,
            retain_hot_days=7,
            now=_NOW,
        )


def test_dry_run_does_not_modify_hot_or_archive_files(tmp_path: Path) -> None:
    hot_path = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    archive_file, manifest = _archived_copy(tmp_path, hot_path)
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (hot_path, archive_file, manifest)
    }

    create_lifecycle_dry_run_plan(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )

    after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before}
    assert after == before


def test_plan_canonical_payload_excludes_generated_at_from_hash(tmp_path: Path) -> None:
    _hot_segment(tmp_path, "2026-06-10.jsonl", mono_ns=10)

    plan = create_lifecycle_dry_run_plan(
        event_log_dir=tmp_path / "hot",
        retain_hot_days=7,
        now=_NOW,
    )

    assert "generated_at" not in plan.canonical_payload()
    assert "plan_hash" not in plan.canonical_payload()


def test_canonical_json_recomputes_plan_hash(tmp_path: Path) -> None:
    _hot_segment(tmp_path, "2026-06-10.jsonl", mono_ns=10)

    plan = create_lifecycle_dry_run_plan(
        event_log_dir=tmp_path / "hot",
        retain_hot_days=7,
        now=_NOW,
    )

    assert hashlib.sha256(plan.canonical_json().encode("utf-8")).hexdigest() == plan.plan_hash


def test_artifact_filename_contains_plan_hash(tmp_path: Path) -> None:
    _hot_segment(tmp_path, "2026-06-10.jsonl", mono_ns=10)

    plan = create_lifecycle_dry_run_plan(
        event_log_dir=tmp_path / "hot",
        retain_hot_days=7,
        now=_NOW,
    )

    assert plan.artifact_filename == f"lifecycle-dry-run-plan-{plan.plan_hash}.json"


def test_collect_hot_segments_public_helper_matches_hot_inventory(tmp_path: Path) -> None:
    _hot_segment(tmp_path, "2026-06-10.jsonl", mono_ns=10)

    segments = collect_hot_segments(tmp_path / "hot")

    assert len(segments) == 1
    assert segments[0].original_relpath == "2026-06-10.jsonl"


def test_naive_now_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        create_lifecycle_dry_run_plan(
            event_log_dir=tmp_path,
            retain_hot_days=7,
            now=datetime(2026, 6, 11, 12, 0),
        )


def test_negative_retain_hot_days_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="retain_hot_days"):
        create_lifecycle_dry_run_plan(
            event_log_dir=tmp_path,
            retain_hot_days=-1,
            now=_NOW,
        )


def test_phase_14_artifacts_activate_epic_71_before_package_work() -> None:
    roots = [
        Path("_bmad-output/planning-artifacts/phase-14-prd-amendment.md"),
        Path("_bmad-output/planning-artifacts/phase-14-architecture-amendment.md"),
        Path("_bmad-output/planning-artifacts/phase-14-epics.md"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in roots)

    assert "docs/status-only safety slice" not in combined
    assert "No `packages/replay` dry-run planner module" not in combined
    assert "Epic 71" in combined
    assert "package-only" in combined


def test_replay_init_preserves_existing_public_exports_and_adds_lifecycle_exports() -> None:
    existing_exports = {
        "replay_events",
        "replay_events_stream",
        "validate_replay",
        "SegmentKey",
        "HOT_ONLY_REPLAY",
    }
    lifecycle_exports = {
        "LifecycleArchiveCoverage",
        "LifecycleBlocker",
        "LifecycleDecision",
        "LifecycleDryRunPlan",
        "LifecycleRetentionPolicy",
        "LifecycleSegmentIdentity",
        "create_lifecycle_dry_run_plan",
    }

    assert existing_exports <= set(replay.__all__)
    assert lifecycle_exports <= set(replay.__all__)


def test_lifecycle_decision_statuses_are_retained_eligible_or_blocked(tmp_path: Path) -> None:
    _hot_segment(tmp_path, "2026-06-10.jsonl", mono_ns=10)
    plan = create_lifecycle_dry_run_plan(
        event_log_dir=tmp_path / "hot",
        retain_hot_days=7,
        now=_NOW,
    )

    assert {decision.status for decision in plan.decisions} <= {
        "retained",
        "eligible",
        "blocked",
    }


def test_lifecycle_public_api_contract() -> None:
    signature = inspect.signature(create_lifecycle_dry_run_plan)

    assert list(signature.parameters) == [
        "event_log_dir",
        "archive_manifest_path",
        "retain_hot_days",
        "now",
        "safety_policy_version",
    ]
    assert all(
        param.kind is inspect.Parameter.KEYWORD_ONLY for param in signature.parameters.values()
    )
    for cls in (
        LifecycleArchiveCoverage,
        LifecycleBlocker,
        LifecycleDecision,
        LifecycleDryRunPlan,
        LifecycleRetentionPolicy,
        LifecycleSegmentIdentity,
    ):
        assert cast(Any, cls).__dataclass_params__.frozen is True


def test_no_destructive_public_names_or_lifecycle_calls() -> None:
    forbidden_names = {"apply", "delete", "truncate", "move", "rewrite", "chmod", "prune_apply"}
    forbidden_calls = (".unlink(", ".remove(", ".truncate(", ".replace(", ".rename(", ".chmod(")
    public_names = {name for name in dir(replay) if not name.startswith("_")} | set(
        getattr(replay, "__all__", ())
    )
    source = Path("packages/replay/src/replay/lifecycle.py").read_text(encoding="utf-8")

    assert forbidden_names.isdisjoint(public_names)
    assert all(call not in source for call in forbidden_calls)


def test_destructive_lifecycle_route_is_approval_gated_only() -> None:
    route_source = Path("services/registry-api/src/registry_api/routes/replay.py").read_text(
        encoding="utf-8"
    )

    assert "/events/replay/lifecycle/plans/{plan_hash}/apply" in route_source
    assert "_require_snapshot_create_authorized(request)" in route_source
    assert "approve_lifecycle_plan" in route_source
    forbidden_route_fragments = (
        "prune_apply",
        "delete_lifecycle",
        "truncate_lifecycle",
        "rewrite_lifecycle",
        "chmod_lifecycle",
    )
    assert all(fragment not in route_source for fragment in forbidden_route_fragments)


def _assert_json_safe(value: Any) -> None:
    json.dumps(value)


def test_canonical_payload_is_json_safe(tmp_path: Path) -> None:
    _hot_segment(tmp_path, "2026-06-10.jsonl", mono_ns=10)
    plan = create_lifecycle_dry_run_plan(
        event_log_dir=tmp_path / "hot",
        retain_hot_days=7,
        now=_NOW,
    )

    _assert_json_safe(plan.canonical_payload())


# ---------------------------------------------------------------------------
# Epic 129 destructive lifecycle mutation controls
# ---------------------------------------------------------------------------

from replay.lifecycle import (  # noqa: E402 — appended Epic 129 regression imports
    UNSUPPORTED_LIFECYCLE_MUTATION_CLASSES,
    LifecycleDryRunArtifact,
    LifecycleMutationError,
    apply_lifecycle_plan,
    approve_lifecycle_plan,
    get_lifecycle_plan_status,
    list_lifecycle_mutations,
    record_lifecycle_dry_run,
    rollback_lifecycle_plan,
)


def _record_validated_lifecycle_dry_run(**kwargs: Any) -> LifecycleDryRunArtifact:
    kwargs.setdefault("replay_validation_ref", "replay-validation:test-pass")
    kwargs.setdefault("replay_validation_status", "passed")
    return record_lifecycle_dry_run(**kwargs)


def test_epic129_persisted_dry_run_writes_audit_and_no_mutation(tmp_path: Path) -> None:
    hot_path = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    _, manifest = _archived_copy(tmp_path, hot_path)

    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
        trace_id=_TRACE_ID,
        request_id=_REQUEST_ID,
    )

    assert hot_path.exists()
    assert artifact.expected_mutations[0]["mutation_class"] == "prune_hot_segment"
    status = get_lifecycle_plan_status(event_log_dir=tmp_path / "hot", plan_hash=artifact.plan_hash)
    assert status["status"] == "dry_run_recorded"
    assert status["affected_count"] == 1
    journal = cast(list[dict[str, Any]], status["journal"])
    assert journal[0]["state"] == "dry_run_recorded"
    assert journal[0]["trace_id"] == _TRACE_ID


def test_epic129_apply_requires_approval_and_rollback_evidence(tmp_path: Path) -> None:
    hot_path = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    _, manifest = _archived_copy(tmp_path, hot_path)
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )

    with pytest.raises(LifecycleMutationError) as excinfo:
        apply_lifecycle_plan(
            event_log_dir=tmp_path / "hot",
            plan_hash=artifact.plan_hash,
            idempotency_key="idem-1",
            now=_NOW,
        )

    assert excinfo.value.code == "approval_missing"
    assert hot_path.exists()


def test_epic129_apply_quarantines_only_eligible_and_is_idempotent(tmp_path: Path) -> None:
    old_hot = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    recent_hot = _hot_segment(tmp_path, "2026-06-10.jsonl", mono_ns=10)
    _, manifest = _archived_copy(tmp_path, old_hot)
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )
    approve_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        operator_identity="operator-1",
        approval_event_ref="approval.granted:e-1",
        now=_NOW,
    )

    result = apply_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        idempotency_key="idem-apply",
        now=_NOW,
    )
    replayed = apply_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        idempotency_key="idem-apply",
        now=_NOW,
    )

    assert result.status == "apply_succeeded"
    assert replayed.replayed is True
    assert not old_hot.exists()
    assert (tmp_path / "hot" / ".lifecycle-trash" / artifact.plan_hash / old_hot.name).exists()
    assert recent_hot.exists()
    status = get_lifecycle_plan_status(event_log_dir=tmp_path / "hot", plan_hash=artifact.plan_hash)
    states = [row["state"] for row in cast(list[dict[str, Any]], status["journal"])]
    assert "approved" in states
    assert "apply_succeeded" in states


def test_epic129_apply_revalidates_archive_coverage_missing(tmp_path: Path) -> None:
    old_hot = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    archive_file, manifest = _archived_copy(tmp_path, old_hot)
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )
    approve_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        operator_identity="operator-1",
        approval_event_ref="approval.granted:e-1",
        now=_NOW,
    )
    archive_file.unlink()

    with pytest.raises(LifecycleMutationError) as excinfo:
        apply_lifecycle_plan(
            event_log_dir=tmp_path / "hot",
            plan_hash=artifact.plan_hash,
            idempotency_key="idem-missing-archive",
            now=_NOW,
        )

    assert excinfo.value.code == "archive_coverage_not_current"
    assert old_hot.exists()


def test_epic129_apply_revalidates_archive_coverage_hash(tmp_path: Path) -> None:
    old_hot = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    archive_file, manifest = _archived_copy(tmp_path, old_hot)
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )
    approve_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        operator_identity="operator-1",
        approval_event_ref="approval.granted:e-1",
        now=_NOW,
    )
    archive_file.write_bytes(b"corrupt\n")

    with pytest.raises(LifecycleMutationError) as excinfo:
        apply_lifecycle_plan(
            event_log_dir=tmp_path / "hot",
            plan_hash=artifact.plan_hash,
            idempotency_key="idem-corrupt-archive",
            now=_NOW,
        )

    assert excinfo.value.code == "archive_coverage_not_current"
    assert old_hot.exists()


def test_epic129_rollback_restores_and_is_idempotent(tmp_path: Path) -> None:
    old_hot = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    _, manifest = _archived_copy(tmp_path, old_hot)
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )
    approve_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        operator_identity="operator-1",
        approval_event_ref="approval.granted:e-1",
        now=_NOW,
    )
    apply_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        idempotency_key="idem-apply",
        now=_NOW,
    )

    result = rollback_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        idempotency_key="idem-rollback",
        rollback_event_ref="rollback.approved:e-2",
        now=_NOW,
    )
    replayed = rollback_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        idempotency_key="idem-rollback",
        rollback_event_ref="rollback.approved:e-2",
        now=_NOW,
    )

    assert result.status == "rollback_succeeded"
    assert replayed.replayed is True
    assert old_hot.exists()
    status = get_lifecycle_plan_status(event_log_dir=tmp_path / "hot", plan_hash=artifact.plan_hash)
    assert status["rolled_back"] is True
    journal = cast(list[dict[str, Any]], status["journal"])
    assert any(row["state"] == "rollback_succeeded" for row in journal)


def test_epic129_rollback_hash_mismatch_keeps_corrupt_quarantine_out_of_hot_log(
    tmp_path: Path,
) -> None:
    old_hot = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    original_bytes = old_hot.read_bytes()
    _, manifest = _archived_copy(tmp_path, old_hot)
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )
    approve_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        operator_identity="operator-1",
        approval_event_ref="approval.granted:e-1",
        now=_NOW,
    )
    apply_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        idempotency_key="idem-apply",
        now=_NOW,
    )
    quarantine = tmp_path / "hot" / ".lifecycle-trash" / artifact.plan_hash / old_hot.name
    quarantine.write_bytes(b"corrupt\n")

    with pytest.raises(LifecycleMutationError) as excinfo:
        rollback_lifecycle_plan(
            event_log_dir=tmp_path / "hot",
            plan_hash=artifact.plan_hash,
            idempotency_key="idem-rollback-corrupt",
            rollback_event_ref="rollback.approved:e-corrupt",
            now=_NOW,
        )

    assert excinfo.value.code == "rollback_hash_mismatch"
    assert not old_hot.exists()
    assert quarantine.exists()
    assert quarantine.read_bytes() != original_bytes
    status = get_lifecycle_plan_status(event_log_dir=tmp_path / "hot", plan_hash=artifact.plan_hash)
    assert status["status"] == "rollback_failed_partial"
    assert status["problem_code"] == "rollback_hash_mismatch"


def test_epic129_rollback_target_drift_does_not_overwrite_hot_log(tmp_path: Path) -> None:
    old_hot = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    _, manifest = _archived_copy(tmp_path, old_hot)
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )
    approve_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        operator_identity="operator-1",
        approval_event_ref="approval.granted:e-1",
        now=_NOW,
    )
    apply_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        idempotency_key="idem-apply",
        now=_NOW,
    )
    old_hot.write_bytes(b"new divergent hot bytes\n")
    quarantine = tmp_path / "hot" / ".lifecycle-trash" / artifact.plan_hash / old_hot.name
    quarantine_bytes = quarantine.read_bytes()

    with pytest.raises(LifecycleMutationError) as excinfo:
        rollback_lifecycle_plan(
            event_log_dir=tmp_path / "hot",
            plan_hash=artifact.plan_hash,
            idempotency_key="idem-rollback-drift",
            rollback_event_ref="rollback.approved:e-drift",
            now=_NOW,
        )

    assert excinfo.value.code == "rollback_target_drift"
    assert old_hot.read_bytes() == b"new divergent hot bytes\n"
    assert quarantine.read_bytes() == quarantine_bytes
    status = get_lifecycle_plan_status(event_log_dir=tmp_path / "hot", plan_hash=artifact.plan_hash)
    assert status["status"] == "rollback_failed_partial"
    assert status["problem_code"] == "rollback_target_drift"


def test_epic129_rollback_rejects_canonical_plan_tampering(tmp_path: Path) -> None:
    old_hot = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    _, manifest = _archived_copy(tmp_path, old_hot)
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )
    approve_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        operator_identity="operator-1",
        approval_event_ref="approval.granted:e-1",
        now=_NOW,
    )
    apply_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        idempotency_key="idem-apply",
        now=_NOW,
    )
    plan_path = Path(artifact.artifact_path)
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["canonical_plan"]["decisions"] = []
    plan_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LifecycleMutationError) as excinfo:
        rollback_lifecycle_plan(
            event_log_dir=tmp_path / "hot",
            plan_hash=artifact.plan_hash,
            idempotency_key="idem-rollback-tamper",
            rollback_event_ref="rollback.approved:e-tamper",
            now=_NOW,
        )

    assert excinfo.value.code == "plan_hash_mismatch"
    assert not old_hot.exists()
    assert (tmp_path / "hot" / ".lifecycle-trash" / artifact.plan_hash / old_hot.name).exists()


def test_epic129_target_identity_drift_fails_closed(tmp_path: Path) -> None:
    hot_path = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    _, manifest = _archived_copy(tmp_path, hot_path)
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )
    approve_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        operator_identity="operator-1",
        approval_event_ref="approval.granted:e-1",
        now=_NOW,
    )
    hot_path.write_bytes(hot_path.read_bytes() + b"\n")

    with pytest.raises(LifecycleMutationError) as excinfo:
        apply_lifecycle_plan(
            event_log_dir=tmp_path / "hot",
            plan_hash=artifact.plan_hash,
            idempotency_key="idem-drift",
            now=_NOW,
        )

    assert excinfo.value.code == "target_hash_mismatch"
    assert hot_path.exists()


@pytest.mark.parametrize("mutation_class", sorted(UNSUPPORTED_LIFECYCLE_MUTATION_CLASSES))
def test_epic129_unsupported_mutation_classes_fail_closed(
    tmp_path: Path, mutation_class: str
) -> None:
    hot_path = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    _, manifest = _archived_copy(tmp_path, hot_path)
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )
    approve_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        operator_identity="operator-1",
        approval_event_ref="approval.granted:e-1",
        now=_NOW,
    )

    with pytest.raises(LifecycleMutationError) as excinfo:
        apply_lifecycle_plan(
            event_log_dir=tmp_path / "hot",
            plan_hash=artifact.plan_hash,
            idempotency_key=f"idem-{mutation_class}",
            mutation_class=mutation_class,
            now=_NOW,
        )

    assert excinfo.value.code == "unsupported_mutation_class"
    assert hot_path.exists()


def test_epic129_status_rebuilds_from_durable_artifacts(tmp_path: Path) -> None:
    hot_path = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    _, manifest = _archived_copy(tmp_path, hot_path)
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )

    rows = list_lifecycle_mutations(event_log_dir=tmp_path / "hot")
    status = get_lifecycle_plan_status(event_log_dir=tmp_path / "hot", plan_hash=artifact.plan_hash)

    assert rows[0]["plan_hash"] == artifact.plan_hash
    journal = cast(list[dict[str, Any]], status["journal"])
    assert journal[0]["action"] == "dry_run"


def test_epic129_mutation_listing_exposes_only_public_status_fields(tmp_path: Path) -> None:
    hot_path = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    _, manifest = _archived_copy(tmp_path, hot_path)
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )
    approve_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        operator_identity="operator-1",
        approval_event_ref="approval.granted:e-1",
        now=_NOW,
    )
    apply_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        idempotency_key="idem-apply",
        now=_NOW,
    )

    row = list_lifecycle_mutations(event_log_dir=tmp_path / "hot")[0]

    assert row["status"] == "apply_succeeded"
    assert "operator_identity" not in row
    assert "approval_event_ref" not in row
    assert "last_idempotency_key" not in row
    assert "detail" not in row
    assert "partial_moved" not in row


def test_epic129_evidence_override_outside_event_log_fails_closed(tmp_path: Path) -> None:
    hot_path = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    _, manifest = _archived_copy(tmp_path, hot_path)
    outside = tmp_path / "external-evidence"

    with pytest.raises(LifecycleMutationError) as excinfo:
        _record_validated_lifecycle_dry_run(
            event_log_dir=tmp_path / "hot",
            archive_manifest_path=manifest,
            retain_hot_days=7,
            evidence_dir=outside,
            now=_NOW,
        )

    assert excinfo.value.code == "evidence_dir_outside_event_log"
    assert hot_path.exists()
    assert not outside.exists()


def test_epic129_status_exposes_stale_freshness_for_expired_evidence(tmp_path: Path) -> None:
    hot_path = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    _, manifest = _archived_copy(tmp_path, hot_path)
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        expires_in_seconds=1,
        now=_NOW,
    )

    status = get_lifecycle_plan_status(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        now=datetime(2026, 7, 4, 13, 0, 2, tzinfo=UTC),
    )
    rows = list_lifecycle_mutations(
        event_log_dir=tmp_path / "hot",
        now=datetime(2026, 7, 4, 13, 0, 2, tzinfo=UTC),
    )

    assert status["plan_expires_at"] == artifact.expires_at
    assert status["freshness_state"] == "stale"
    assert rows[0]["freshness_state"] == "stale"


def test_epic129_global_mutation_lock_blocks_apply(tmp_path: Path) -> None:
    hot_path = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    _, manifest = _archived_copy(tmp_path, hot_path)
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )
    approve_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        operator_identity="operator-1",
        approval_event_ref="approval.granted:e-1",
        now=_NOW,
    )
    lock_dir = (
        tmp_path / "hot" / "lifecycle-evidence" / "locks" / "event-log-lifecycle-mutation.lock"
    )
    lock_dir.mkdir()

    with pytest.raises(LifecycleMutationError) as excinfo:
        apply_lifecycle_plan(
            event_log_dir=tmp_path / "hot",
            plan_hash=artifact.plan_hash,
            idempotency_key="idem-locked",
            now=_NOW,
        )

    assert excinfo.value.code == "lifecycle_mutation_locked"
    assert hot_path.exists()


def test_epic129_mutation_helpers_remain_out_of_root_replay_namespace() -> None:
    assert replay.create_lifecycle_dry_run_plan is create_lifecycle_dry_run_plan
    for name in (
        "record_lifecycle_dry_run",
        "approve_lifecycle_plan",
        "apply_lifecycle_plan",
        "rollback_lifecycle_plan",
        "get_lifecycle_plan_status",
        "list_lifecycle_mutations",
        "LifecycleMutationError",
    ):
        assert not hasattr(replay, name)
        assert name not in replay.__all__


def test_epic129_dry_run_requires_current_replay_validation(tmp_path: Path) -> None:
    hot_path = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    _, manifest = _archived_copy(tmp_path, hot_path)

    with pytest.raises(LifecycleMutationError) as excinfo:
        record_lifecycle_dry_run(
            event_log_dir=tmp_path / "hot",
            archive_manifest_path=manifest,
            retain_hot_days=7,
            now=_NOW,
        )

    assert excinfo.value.code == "replay_validation_not_current"
    assert hot_path.exists()


def test_epic129_tampered_expected_mutations_fail_closed(tmp_path: Path) -> None:
    eligible = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    retained = _hot_segment(tmp_path, "2026-07-03.jsonl", mono_ns=2)
    _, manifest = _archived_copy(tmp_path, eligible)
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )
    approve_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        operator_identity="operator-1",
        approval_event_ref="approval.granted:e-1",
        now=_NOW,
    )
    plan_path = Path(artifact.artifact_path)
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["expected_mutations"] = [
        {
            "mutation_class": "prune_hot_segment",
            "original_relpath": retained.name,
            "logical_date": retained.stem,
            "first_sequence": 2,
            "last_sequence": 2,
            "sha256": _sha256(retained),
            "event_count": 1,
        }
    ]
    plan_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(LifecycleMutationError) as excinfo:
        apply_lifecycle_plan(
            event_log_dir=tmp_path / "hot",
            plan_hash=artifact.plan_hash,
            idempotency_key="idem-tamper",
            now=_NOW,
        )

    assert excinfo.value.code == "expected_mutations_mismatch"
    assert eligible.exists()
    assert retained.exists()


def test_epic129_partial_apply_failure_restores_moved_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    second = _hot_segment(tmp_path, "2026-06-02.jsonl", mono_ns=2)
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    for hot_path in (first, second):
        (archive_dir / hot_path.name).write_bytes(hot_path.read_bytes())
    segments = []
    for segment in collect_hot_segments(archive_dir):
        segments.append(
            {
                "logical_date": segment.key.logical_date,
                "original_relpath": segment.original_relpath,
                "archive_relpath": segment.original_relpath,
                "sha256": segment.sha256,
                "event_count": segment.event_count,
                "first_sequence": segment.key.first_sequence,
                "last_sequence": segment.key.last_sequence,
                "archived_at": "2026-06-11T00:00:00Z",
                "actor_id": "pytest",
            }
        )
    manifest = archive_dir / "lifecycle-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "manifest_id": "m-partial",
                "created_at": "2026-06-11T00:00:00Z",
                "created_by": "pytest",
                "segments": segments,
            }
        ),
        encoding="utf-8",
    )
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )
    approve_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        operator_identity="operator-1",
        approval_event_ref="approval.granted:e-1",
        now=_NOW,
    )
    real_move = shutil.move
    calls = 0

    def flaky_move(src: str, dst: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second move failure")
        return str(real_move(src, dst))

    monkeypatch.setattr("replay.lifecycle.shutil.move", flaky_move)

    with pytest.raises(LifecycleMutationError) as excinfo:
        apply_lifecycle_plan(
            event_log_dir=tmp_path / "hot",
            plan_hash=artifact.plan_hash,
            idempotency_key="idem-partial",
            now=_NOW,
        )

    assert excinfo.value.code == "apply_failed_restored"
    assert first.exists()
    assert second.exists()
    status = get_lifecycle_plan_status(event_log_dir=tmp_path / "hot", plan_hash=artifact.plan_hash)
    assert status["status"] == "apply_failed_restored"


def test_epic129_partial_apply_restore_does_not_overwrite_divergent_hot_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _hot_segment(tmp_path, "2026-06-01.jsonl", mono_ns=1)
    second = _hot_segment(tmp_path, "2026-06-02.jsonl", mono_ns=2)
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    for hot_path in (first, second):
        (archive_dir / hot_path.name).write_bytes(hot_path.read_bytes())
    segments = []
    for segment in collect_hot_segments(archive_dir):
        segments.append(
            {
                "logical_date": segment.key.logical_date,
                "original_relpath": segment.original_relpath,
                "archive_relpath": segment.original_relpath,
                "sha256": segment.sha256,
                "event_count": segment.event_count,
                "first_sequence": segment.key.first_sequence,
                "last_sequence": segment.key.last_sequence,
                "archived_at": "2026-06-11T00:00:00Z",
                "actor_id": "pytest",
            }
        )
    manifest = archive_dir / "lifecycle-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "manifest_id": "m-partial-drift",
                "created_at": "2026-06-11T00:00:00Z",
                "created_by": "pytest",
                "segments": segments,
            }
        ),
        encoding="utf-8",
    )
    artifact = _record_validated_lifecycle_dry_run(
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
        retain_hot_days=7,
        now=_NOW,
    )
    approve_lifecycle_plan(
        event_log_dir=tmp_path / "hot",
        plan_hash=artifact.plan_hash,
        operator_identity="operator-1",
        approval_event_ref="approval.granted:e-1",
        now=_NOW,
    )
    real_move = shutil.move
    calls = 0

    def flaky_move(src: str, dst: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            result = str(real_move(src, dst))
            first.write_bytes(b"new divergent hot bytes\n")
            return result
        if calls == 2:
            raise OSError("simulated second move failure")
        return str(real_move(src, dst))

    monkeypatch.setattr("replay.lifecycle.shutil.move", flaky_move)

    with pytest.raises(LifecycleMutationError) as excinfo:
        apply_lifecycle_plan(
            event_log_dir=tmp_path / "hot",
            plan_hash=artifact.plan_hash,
            idempotency_key="idem-partial-drift",
            now=_NOW,
        )

    assert excinfo.value.code == "apply_failed_partial"
    assert first.read_bytes() == b"new divergent hot bytes\n"
    assert second.exists()
    status = get_lifecycle_plan_status(event_log_dir=tmp_path / "hot", plan_hash=artifact.plan_hash)
    assert status["status"] == "apply_failed_partial"
