"""Tests for Story 131.4 deployment change readiness gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_deployment_change_readiness.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_deployment_change_readiness", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_deployment_change_readiness"] = mod
    spec.loader.exec_module(mod)
    return mod


def _copy_live_fixture(tmp_path: Path, mod: object) -> None:
    for rel in [
        mod.CONTRACT_PATH,  # type: ignore[attr-defined]
        mod.CREDENTIAL_CONTRACT_PATH,  # type: ignore[attr-defined]
        mod.PRODUCTION_OPS_PATH,  # type: ignore[attr-defined]
        mod.OPERATOR_RUNBOOK_PATH,  # type: ignore[attr-defined]
        mod.DEPLOYMENT_GUIDE_PATH,  # type: ignore[attr-defined]
        mod.BACKUP_RESTORE_PATH,  # type: ignore[attr-defined]
        mod.JUSTFILE_PATH,  # type: ignore[attr-defined]
        mod.DIGEST_COMPOSE_PATH,  # type: ignore[attr-defined]
        mod.BASE_COMPOSE_PATH,  # type: ignore[attr-defined]
        mod.MACOS_COMPOSE_PATH,  # type: ignore[attr-defined]
        mod.ARTIFACT_PATH,  # type: ignore[attr-defined]
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


def test_digest_deploy_without_verify_images_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    justfile = tmp_path / mod.JUSTFILE_PATH  # type: ignore[attr-defined]
    justfile.write_text(
        justfile.read_text(encoding="utf-8").replace(
            "deploy-vps-digest: verify-images", "deploy-vps-digest:", 1
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("deploy-vps-digest must depend on verify-images" in v.message for v in violations)


def test_digest_overlay_default_fallback_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    compose = tmp_path / mod.DIGEST_COMPOSE_PATH  # type: ignore[attr-defined]
    compose.write_text(
        compose.read_text(encoding="utf-8").replace(
            "${OMB_IMAGE_DIGEST_registry_api:?set OMB_IMAGE_DIGEST_registry_api",
            "${OMB_IMAGE_DIGEST_registry_api:-sha256:0000000000000000000000000000000000000000000000000000000000000000",
            1,
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("default fallback" in v.message or "fail-loud" in v.message for v in violations)


def test_contract_missing_required_evidence_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    contract = tmp_path / mod.CONTRACT_PATH  # type: ignore[attr-defined]
    data = json.loads(contract.read_text(encoding="utf-8"))
    data["required_evidence"] = [x for x in data["required_evidence"] if "post-deploy" not in x]
    contract.write_text(json.dumps(data), encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("required_evidence missing" in v.message for v in violations)
