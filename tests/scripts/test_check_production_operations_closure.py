"""Tests for Story 131.6 production operations readiness closure gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_production_operations_closure.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "check_production_operations_closure", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_production_operations_closure"] = mod
    spec.loader.exec_module(mod)
    return mod


def _copy_live_fixture(tmp_path: Path, mod: object) -> None:
    for rel in [
        mod.CONTRACT_PATH,  # type: ignore[attr-defined]
        mod.FEATURE_STATUS_PATH,  # type: ignore[attr-defined]
        mod.PRODUCTION_OPS_PATH,  # type: ignore[attr-defined]
        mod.OPERATOR_RUNBOOK_PATH,  # type: ignore[attr-defined]
        mod.SPRINT_STATUS_PATH,  # type: ignore[attr-defined]
        mod.ARTIFACT_PATH,  # type: ignore[attr-defined]
        mod.CI_PATH,  # type: ignore[attr-defined]
        mod.JUSTFILE_PATH,  # type: ignore[attr-defined]
        Path("docs/production-credential-inventory.json"),
        Path("docs/github-write-activation-readiness.json"),
        Path("docs/deployment-change-readiness.json"),
        Path("docs/production-command-surface-readiness.json"),
        Path("scripts/check_production_credentials.py"),
        Path("scripts/check_github_write_activation.py"),
        Path("scripts/check_deployment_change_readiness.py"),
        Path("scripts/check_production_command_surface.py"),
        Path(
            "_bmad-output/implementation-artifacts/131-1-production-operations-runbook-preflight-contract.md"
        ),
        Path(
            "_bmad-output/implementation-artifacts/131-2-credential-provisioning-scoping-rotation-revocation.md"
        ),
        Path("_bmad-output/implementation-artifacts/131-3-github-write-activation-readiness.md"),
        Path("_bmad-output/implementation-artifacts/131-4-deployment-change-control-readiness.md"),
        Path("_bmad-output/implementation-artifacts/131-5-production-command-surface-readiness.md"),
    ]:
        src = REPO_ROOT / rel
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def test_self_test_passes() -> None:
    mod = _load_module()
    assert mod._self_test() == 0  # type: ignore[attr-defined]


def test_live_contract_is_clean() -> None:
    mod = _load_module()
    assert mod.main([]) == 0  # type: ignore[attr-defined]


def test_missing_ci_gate_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    ci = tmp_path / mod.CI_PATH  # type: ignore[attr-defined]
    ci.write_text(
        ci.read_text(encoding="utf-8").replace("scripts/check_production_command_surface.py", ""),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("CI missing gate" in v.message for v in violations)


def test_live_activation_overclaim_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    feature = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    feature.write_text(
        feature.read_text(encoding="utf-8") + "\nReal GitHub writes are enabled for production.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("overclaim" in v.message for v in violations)


def test_contract_missing_required_story_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    contract = tmp_path / mod.CONTRACT_PATH  # type: ignore[attr-defined]
    data = json.loads(contract.read_text(encoding="utf-8"))
    data["required_story_evidence"] = [
        entry for entry in data["required_story_evidence"] if entry["story"] != "131.5"
    ]
    contract.write_text(json.dumps(data), encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("required_story_evidence missing" in v.message for v in violations)
