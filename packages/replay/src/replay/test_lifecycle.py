"""Tests for Phase 14 lifecycle dry-run planning."""

from __future__ import annotations

import hashlib
import inspect
import json
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


def test_no_route_or_api_contract_files_changed() -> None:
    changed = _git_changed_files()

    assert "docs/api-contracts.md" not in changed
    assert all(
        not path.startswith("services/registry-api/src/registry_api/routes/") for path in changed
    )


def _git_changed_files() -> set[str]:
    import subprocess

    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return set(result.stdout.splitlines())


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
