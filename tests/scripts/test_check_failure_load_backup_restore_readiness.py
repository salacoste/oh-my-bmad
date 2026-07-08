"""Tests for Story 132.7 failure/load/backup/restore readiness gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_failure_load_backup_restore_readiness.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "check_failure_load_backup_restore_readiness", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_failure_load_backup_restore_readiness"] = mod
    spec.loader.exec_module(mod)
    return mod


def _copy_live_fixture(tmp_path: Path, mod: object) -> None:
    paths = set(mod.REQUIRED_FILES) | set(mod.SECRET_SCAN_PATHS) | {mod.ARTIFACT_PATH}  # type: ignore[attr-defined]
    for rel in paths:
        src = REPO_ROOT / rel
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _load_contract(tmp_path: Path, mod: object) -> dict[str, Any]:
    raw: object = json.loads((tmp_path / mod.CONTRACT_PATH).read_text(encoding="utf-8"))  # type: ignore[attr-defined]
    assert isinstance(raw, dict)
    return cast("dict[str, Any]", raw)


def _write_contract(tmp_path: Path, mod: object, data: dict[str, Any]) -> None:
    (tmp_path / mod.CONTRACT_PATH).write_text(json.dumps(data, indent=2), encoding="utf-8")  # type: ignore[attr-defined]


def test_self_test_passes() -> None:
    mod = _load_module()
    assert mod._self_test() == 0  # type: ignore[attr-defined]


def test_live_contract_is_clean() -> None:
    mod = _load_module()
    assert mod.main([]) == 0  # type: ignore[attr-defined]


def test_missing_ci_or_just_gate_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    for rel in [mod.JUSTFILE_PATH, mod.CI_PATH]:  # type: ignore[attr-defined]
        target = tmp_path / rel
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                mod.CHECKER_COMMAND,
                "uv run python scripts/other.py",  # type: ignore[attr-defined]
            ),
            encoding="utf-8",
        )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("missing required reference" in v.message for v in violations)


def test_live_activation_overclaim_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["production_activation"] = "active"
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("production_activation" in v.message for v in violations)


def test_forbidden_production_surface_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    policy = data["execution_policy"]
    assert isinstance(policy, dict)
    policy["production_mutation"] = True
    load = data["load_validation"]
    assert isinstance(load, dict)
    load["external_production_load"] = True
    backup = data["backup_restore_validation"]
    assert isinstance(backup, dict)
    backup["production_restore"] = True
    backup["backup_pruning"] = True
    _write_contract(tmp_path, mod, data)

    surface_mutations = {
        mod.OPERATOR_RUNBOOK_PATH: "\nStory 132.7 provisioning_enabled: true\n",
        mod.PRODUCTION_OPS_PATH: "\nStory 132.7 runtime_audit_emitter_enabled: true\n",
        mod.FEATURE_STATUS_PATH: "\nStory 132.7 live_load_generation: true\n",
        mod.BACKUP_RESTORE_PATH: "\nStory 132.7 live_restore_execution: true\n",
        mod.ARTIFACT_PATH: "\nStory 132.7 production_host_mutation: true\n",
        mod.SPRINT_STATUS_PATH: "\n132-7 live_drill_execution: true\n",
    }
    for relpath, mutation in surface_mutations.items():
        target = tmp_path / relpath
        target.write_text(target.read_text(encoding="utf-8") + mutation, encoding="utf-8")

    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    messages = [v.message for v in violations]
    assert any("production host mutation" in message for message in messages)
    assert any("provisioning" in message for message in messages)
    assert any("runtime audit emitters" in message for message in messages)
    assert any("live load" in message for message in messages)
    assert any("live restore" in message for message in messages)
    assert any("live drill execution" in message for message in messages)
    assert any("backup_pruning=false" in message for message in messages)
    assert any("production_restore=false" in message for message in messages)


def test_secret_like_value_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    artifact = tmp_path / mod.ARTIFACT_PATH  # type: ignore[attr-defined]
    artifact.write_text(
        artifact.read_text(encoding="utf-8") + "\npassword=abcdefghijklmnopqrstuvwx123456\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)


def test_missing_required_failure_scenario_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    scenarios = data["failure_scenarios"]
    assert isinstance(scenarios, list)
    data["failure_scenarios"] = [
        item
        for item in scenarios
        if isinstance(item, dict) and item.get("id") != "network_partition"
    ]
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("network_partition" in v.message for v in violations)


def test_live_execution_destructive_restore_load_generation_policy_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    policy = data["execution_policy"]
    assert isinstance(policy, dict)
    policy["live_drill_execution"] = True
    policy["destructive_restore"] = True
    policy["load_generation"] = True
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    messages = [v.message for v in violations]
    assert any("live_drill_execution" in message for message in messages)
    assert any("destructive_restore" in message for message in messages)
    assert any("load_generation" in message for message in messages)


def test_missing_latency_error_backpressure_metrics_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    load = data["load_validation"]
    assert isinstance(load, dict)
    metrics = load["metrics"]
    assert isinstance(metrics, dict)
    metrics.pop("latency")
    metrics.pop("error")
    metrics.pop("backpressure")
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    message = "\n".join(v.message for v in violations)
    assert "latency" in message
    assert "error" in message
    assert "backpressure" in message


def test_missing_load_safety_and_trace_requirements_fail(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    load = data["load_validation"]
    assert isinstance(load, dict)
    load["bounded_synthetic_load_only"] = False
    load["no_external_production_load"] = False
    load["target_surfaces"] = []
    load.pop("pool_saturation_thresholds")
    load.pop("rate_limit_preservation")
    load.pop("trace_correlation")
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    message = "\n".join(v.message for v in violations)
    assert "bounded_synthetic_load_only" in message
    assert "no_external_production_load" in message
    assert "registry-api" in message
    assert "pool_saturation_thresholds" in message
    assert "rate_limit_preservation" in message
    assert "trace_correlation" in message


def test_missing_backup_restore_requirements_fail(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    backup = data["backup_restore_validation"]
    assert isinstance(backup, dict)
    backup.pop("checksum_manifest_validation")
    backup.pop("isolated_restore")
    backup.pop("destructive_restore_confirmation")
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    message = "\n".join(v.message for v in violations)
    assert "checksum_manifest_validation" in message
    assert "isolated_restore" in message
    assert "destructive_restore_confirmation" in message


def test_missing_sanitized_log_trace_recovery_observability_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    obs = data["observability_and_audit"]
    assert isinstance(obs, dict)
    obs.pop("sanitized_logs")
    obs.pop("trace_ids")
    obs.pop("recovery_timeline")
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    message = "\n".join(v.message for v in violations)
    assert "sanitized_logs" in message
    assert "trace_ids" in message
    assert "recovery_timeline" in message


def test_missing_safety_boundary_or_non_goal_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    safety = data["safety_boundaries"]
    assert isinstance(safety, dict)
    safety["no_runtime_audit_emitter"] = False
    data["non_goals"] = ["no live drill execution"]
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    message = "\n".join(v.message for v in violations)
    assert "no_runtime_audit_emitter" in message
    assert "non_goals" in message
