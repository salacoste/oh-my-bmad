"""Tests for Story 131.5 production command-surface readiness gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_production_command_surface.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_production_command_surface", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_production_command_surface"] = mod
    spec.loader.exec_module(mod)
    return mod


def _copy_path(src: Path, dst: Path) -> None:
    if src.is_dir():
        for child in src.rglob("*"):
            if child.is_dir() or "__pycache__" in child.parts:
                continue
            if child.suffix not in {".py", ".js", ".html", ".json"}:
                continue
            child_dst = dst / child.relative_to(src)
            child_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, child_dst)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _copy_live_fixture(tmp_path: Path, mod: object) -> None:
    for rel in [
        mod.CONTRACT_PATH,  # type: ignore[attr-defined]
        mod.PRODUCTION_OPS_PATH,  # type: ignore[attr-defined]
        mod.OPERATOR_RUNBOOK_PATH,  # type: ignore[attr-defined]
        mod.FEATURE_STATUS_PATH,  # type: ignore[attr-defined]
        mod.CREDENTIAL_CONTRACT_PATH,  # type: ignore[attr-defined]
        mod.DEPLOYMENT_CONTRACT_PATH,  # type: ignore[attr-defined]
        mod.CONSOLE_MAIN_PATH,  # type: ignore[attr-defined]
        mod.CONSOLE_COMMANDS_DIR,  # type: ignore[attr-defined]
        mod.TELEGRAM_LIFESPAN_PATH,  # type: ignore[attr-defined]
        mod.TELEGRAM_HANDLERS_DIR,  # type: ignore[attr-defined]
        mod.DASHBOARD_STATIC_DIR,  # type: ignore[attr-defined]
        mod.REGISTRY_ROUTES_DIR,  # type: ignore[attr-defined]
        mod.ARTIFACT_PATH,  # type: ignore[attr-defined]
    ]:
        _copy_path(REPO_ROOT / rel, tmp_path / rel)


def test_self_test_passes() -> None:
    mod = _load_module()
    assert mod._self_test() == 0  # type: ignore[attr-defined]


def test_live_contract_is_clean() -> None:
    mod = _load_module()
    assert mod.main([]) == 0  # type: ignore[attr-defined]


def test_console_prod_command_module_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    bad = tmp_path / mod.CONSOLE_COMMANDS_DIR / "prod_operation.py"  # type: ignore[attr-defined]
    bad.write_text("def apply():\n    return 'production-operation'\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("unexpected console command modules" in v.message for v in violations)
    assert any("forbidden runtime token" in v.message for v in violations)


def test_dashboard_production_control_token_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    dashboard = tmp_path / mod.DASHBOARD_STATIC_DIR / "index.html"  # type: ignore[attr-defined]
    dashboard.write_text(
        dashboard.read_text(encoding="utf-8") + '<button id="operation-approve">approve</button>',
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("operation-approve" in v.message for v in violations)


def test_contract_missing_required_evidence_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    contract = tmp_path / mod.CONTRACT_PATH  # type: ignore[attr-defined]
    data = json.loads(contract.read_text(encoding="utf-8"))
    data["required_evidence"] = [x for x in data["required_evidence"] if "emergency" not in x]
    contract.write_text(json.dumps(data), encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("required_evidence missing" in v.message for v in violations)
