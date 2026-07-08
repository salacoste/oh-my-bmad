"""Tests for Story 132.6 horizontal scaling readiness gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_horizontal_scaling_readiness.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_horizontal_scaling_readiness", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_horizontal_scaling_readiness"] = mod
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
    lb = data["load_balancer_readiness"]
    assert isinstance(lb, dict)
    lb["external_load_balancer_added"] = True
    lb["host_ports_published"] = True
    lb["external_host_added"] = True
    rollback = data["rollback_and_observability"]
    assert isinstance(rollback, dict)
    rollback["production_host_mutation"] = True
    _write_contract(tmp_path, mod, data)

    surface_mutations = {
        mod.OPERATOR_RUNBOOK_PATH: "\nStory 132.6 provisioning_enabled: true\n",
        mod.PRODUCTION_OPS_PATH: "\nStory 132.6 runtime_audit_emitter_enabled: true\n",
        mod.FEATURE_STATUS_PATH: "\nStory 132.6 live_load_generation: true\n",
        mod.ARTIFACT_PATH: "\nStory 132.6 live_restore_execution: true\n",
        mod.SPRINT_STATUS_PATH: "\n132-6-horizontal-scaling live_scaling_activation: true\n",
    }
    for relpath, mutation in surface_mutations.items():
        target = tmp_path / relpath
        target.write_text(target.read_text(encoding="utf-8") + mutation, encoding="utf-8")

    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    messages = [v.message for v in violations]
    assert any("production host mutation" in message for message in messages)
    assert any("external hosts/ports" in message for message in messages)
    assert any("provisioning" in message for message in messages)
    assert any("runtime audit emitters" in message for message in messages)
    assert any("live load" in message for message in messages)
    assert any("live restore" in message for message in messages)
    assert any("live scaling activation" in message for message in messages)


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


def test_missing_scale_safety_matrix_service_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    matrix = data["scale_safety_matrix"]
    assert isinstance(matrix, dict)
    matrix.pop("registry-api")
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("registry-api" in v.message for v in violations)


def test_incorrect_service_class_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    matrix = data["scale_safety_matrix"]
    assert isinstance(matrix, dict)
    registry_state = matrix["registry-state"]
    assert isinstance(registry_state, dict)
    registry_state["class"] = "stateless_scalable"
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("registry-state class" in v.message for v in violations)


def test_singleton_authority_omission_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    authorities = data["singleton_authorities"]
    assert isinstance(authorities, dict)
    authorities.pop("event_append_authority")
    authorities.pop("alembic_migration_runner")
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("event_append_authority" in v.message for v in violations)
    assert any("alembic_migration_runner" in v.message for v in violations)


def test_coordination_invariant_omission_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    boundaries = data["coordination_boundaries"]
    assert isinstance(boundaries, list)
    data["coordination_boundaries"] = [
        item
        for item in boundaries
        if isinstance(item, dict)
        and item.get("id") not in {"idempotency_shared_storage", "bounded_db_pool_composition"}
    ]
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("idempotency_shared_storage" in v.message for v in violations)
    assert any("bounded_db_pool_composition" in v.message for v in violations)


def test_load_balancer_auth_trace_health_sticky_omission_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    lb = data["load_balancer_readiness"]
    assert isinstance(lb, dict)
    lb["health_readiness_endpoints"] = []
    lb.pop("trace_propagation")
    lb.pop("auth_header_preservation")
    lb.pop("sticky_sessions")
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("health/readiness" in v.message for v in violations)
    assert any("trace_propagation" in v.message for v in violations)
    assert any("auth_header_preservation" in v.message for v in violations)
    assert any("sticky_sessions" in v.message for v in violations)


def test_unsupported_multi_writer_or_external_worker_omission_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    modes = data["unsupported_scaling_modes"]
    assert isinstance(modes, list)
    data["unsupported_scaling_modes"] = [
        item
        for item in modes
        if isinstance(item, dict)
        and item.get("id") not in {"multi_writer_registry_state", "external_worker_pool"}
    ]
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("multi_writer_registry_state" in v.message for v in violations)
    assert any("external_worker_pool" in v.message for v in violations)
