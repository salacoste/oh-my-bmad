"""Tests for Story 130.1 retention policy/object-storage readiness gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_retention_policy_readiness.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_retention_policy_readiness", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_retention_policy_readiness"] = mod
    spec.loader.exec_module(mod)
    return mod


def _copy_live_fixture(tmp_path: Path, mod: object) -> None:
    for rel in [
        mod.CONTRACT_PATH,  # type: ignore[attr-defined]
        mod.PRODUCTION_OPS_PATH,  # type: ignore[attr-defined]
        mod.OPERATOR_RUNBOOK_PATH,  # type: ignore[attr-defined]
        mod.FEATURE_STATUS_PATH,  # type: ignore[attr-defined]
        mod.SPRINT_STATUS_PATH,  # type: ignore[attr-defined]
        mod.PLANNING_PATH,  # type: ignore[attr-defined]
        mod.ARTIFACT_PATH,  # type: ignore[attr-defined]
        mod.STORY_130_2_MODULE_PATH,  # type: ignore[attr-defined]
        mod.STORY_130_2_TEST_PATH,  # type: ignore[attr-defined]
        mod.STORY_130_2_ARTIFACT_PATH,  # type: ignore[attr-defined]
        mod.CI_PATH,  # type: ignore[attr-defined]
        mod.JUSTFILE_PATH,  # type: ignore[attr-defined]
    ]:
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, dst)


def test_self_test_passes() -> None:
    mod = _load_module()
    assert mod._self_test() == 0  # type: ignore[attr-defined]


def test_live_contract_is_clean() -> None:
    mod = _load_module()
    assert mod.main([]) == 0  # type: ignore[attr-defined]


def test_missing_hold_rules_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    contract = tmp_path / mod.CONTRACT_PATH  # type: ignore[attr-defined]
    data = json.loads(contract.read_text(encoding="utf-8"))
    data["policy_required_fields"] = [
        x for x in data["policy_required_fields"] if x != "hold_and_exclusion_rules"
    ]
    contract.write_text(json.dumps(data), encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("policy_required_fields missing" in v.message for v in violations)


def test_forbidden_runtime_runner_file_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    bad = tmp_path / mod.FORBIDDEN_NEW_PATHS[0]  # type: ignore[attr-defined]
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("print('retention job runner')\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("retention job runner" in v.message for v in violations)


def test_missing_ci_wiring_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    ci = tmp_path / mod.CI_PATH  # type: ignore[attr-defined]
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "uv run python scripts/check_retention_policy_readiness.py", "python missing.py"
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(
        ".github/workflows/ci.yml" in v.location and "missing" in v.message for v in violations
    )


def test_missing_story_130_2_evidence_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    contract = tmp_path / mod.CONTRACT_PATH  # type: ignore[attr-defined]
    data = json.loads(contract.read_text(encoding="utf-8"))
    data["story_130_2_evidence"]["authoritative_rules"] = []
    contract.write_text(json.dumps(data), encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("story_130_2_evidence missing rule" in v.message for v in violations)
