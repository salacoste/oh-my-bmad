"""Tests for Story 132.5 operator/dashboard split readiness gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_operator_dashboard_split.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_operator_dashboard_split", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_operator_dashboard_split"] = mod
    spec.loader.exec_module(mod)
    return mod


def _copy_live_fixture(tmp_path: Path, mod: object) -> None:
    paths = set(mod.REQUIRED_FILES) | set(mod.SECRET_SCAN_PATHS) | {mod.ARTIFACT_PATH}  # type: ignore[attr-defined]
    for rel in paths:
        src = REPO_ROOT / rel
        dst = tmp_path / rel
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _load_contract(tmp_path: Path, mod: object) -> dict[str, object]:
    raw: object = json.loads((tmp_path / mod.CONTRACT_PATH).read_text(encoding="utf-8"))  # type: ignore[attr-defined]
    assert isinstance(raw, dict)
    return cast("dict[str, object]", raw)


def _write_contract(tmp_path: Path, mod: object, data: dict[str, object]) -> None:
    (tmp_path / mod.CONTRACT_PATH).write_text(json.dumps(data, indent=2), encoding="utf-8")  # type: ignore[attr-defined]


def test_self_test_passes() -> None:
    mod = _load_module()
    assert mod._self_test() == 0  # type: ignore[attr-defined]


def test_live_contract_is_clean() -> None:
    mod = _load_module()
    assert mod.main([]) == 0  # type: ignore[attr-defined]


def test_overlay_must_use_profile(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    overlay = tmp_path / mod.OVERLAY_PATH  # type: ignore[attr-defined]
    overlay.write_text(
        overlay.read_text(encoding="utf-8").replace(
            'profiles: ["operator-dashboard-split"]', 'profiles: ["other"]'
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("operator-dashboard-split" in v.message for v in violations)


def test_overlay_must_require_operator_dashboard_auth(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    overlay = tmp_path / mod.OVERLAY_PATH  # type: ignore[attr-defined]
    overlay.write_text(
        overlay.read_text(encoding="utf-8").replace(
            "${OPERATOR_DASHBOARD_AUTH_TOKEN:?", "${OPERATOR_DASHBOARD_AUTH_TOKEN:-"
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("OPERATOR_DASHBOARD_AUTH_TOKEN" in v.message for v in violations)


def test_host_ports_forbidden(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    overlay = tmp_path / mod.OVERLAY_PATH  # type: ignore[attr-defined]
    overlay.write_text(
        overlay.read_text(encoding="utf-8") + "\n    ports:\n      - 8080:8080\n", encoding="utf-8"
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("host ports" in v.message for v in violations)


def test_overlay_must_not_target_console_cli_or_dashboard_service(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    overlay = tmp_path / mod.OVERLAY_PATH  # type: ignore[attr-defined]
    overlay.write_text(
        overlay.read_text(encoding="utf-8")
        + '\n  console-cli:\n    profiles: ["operator-dashboard-split"]\n  dashboard:\n    profiles: ["operator-dashboard-split"]\n',
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("console-cli" in v.message for v in violations)
    assert any("dashboard" in v.message for v in violations)


def test_overlay_must_not_target_arbitrary_extra_service(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    overlay = tmp_path / mod.OVERLAY_PATH  # type: ignore[attr-defined]
    overlay.write_text(
        overlay.read_text(encoding="utf-8")
        + '\n  registry-api:\n    profiles: ["operator-dashboard-split"]\n',
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("overlay services must be exactly" in v.message for v in violations)


def test_overlay_must_not_target_inline_extra_service(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    overlay = tmp_path / mod.OVERLAY_PATH  # type: ignore[attr-defined]
    overlay.write_text(
        overlay.read_text(encoding="utf-8") + "\n  registry-api: {}\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("overlay services must be exactly" in v.message for v in violations)


def test_contract_must_list_operator_surfaces(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    profile = data["profile_artifact"]
    assert isinstance(profile, dict)
    profile["compose_services"] = ["telegram-gateway"]
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("compose_services" in v.message for v in violations)


def test_acceptance_dimensions_required(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    readiness = data["readiness_checks"]
    assert isinstance(readiness, dict)
    readiness["validates"] = ["operator/dashboard overlay is opt-in"]
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("trace propagation" in v.message for v in violations)
    assert any("version compatibility" in v.message for v in violations)


def test_core_split_prerequisites_required(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    prereq = data["core_split_prerequisites"]
    assert isinstance(prereq, dict)
    prereq["required_checkers"] = ["scripts/check_registry_remote_postgres_profile.py"]
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("prerequisite" in v.message for v in violations)


def test_dashboard_boundary_is_static_future_ingress_only(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    dashboard = data["dashboard_boundary"]
    assert isinstance(dashboard, dict)
    dashboard["status"] = "metrics_subscriber_live_dashboard"
    dashboard["not_metrics_subscriber"] = False
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("dashboard_boundary.status" in v.message for v in violations)
    assert any("metrics-subscriber" in v.message for v in violations)


def test_auth_policy_must_not_claim_runtime_enforcement(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    auth = data["auth_policy"]
    assert isinstance(auth, dict)
    auth["runtime_auth_enforcement_added"] = True
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("runtime_auth_enforcement_added" in v.message for v in violations)


def test_secret_in_dashboard_static_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.DASHBOARD_STATIC_DIR / "secret-fixture.js"  # type: ignore[attr-defined]
    target.write_text(
        "const token = '" + "ghp_" + "abcdefghijklmnopqrstuvwx" + "';\n", encoding="utf-8"
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)


def test_secret_or_payload_overclaim_in_docs_or_artifact_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    artifact = tmp_path / mod.ARTIFACT_PATH  # type: ignore[attr-defined]
    artifact.write_text(
        artifact.read_text(encoding="utf-8") + "\npassword=abcdefghijklmnopqrstuvwx123456\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)


def test_secret_in_story_docs_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    runbook = tmp_path / mod.OPERATOR_RUNBOOK_PATH  # type: ignore[attr-defined]
    runbook.write_text(
        runbook.read_text(encoding="utf-8") + "\npassword=abcdefghijklmnopqrstuvwx123456\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)


def test_live_activation_and_external_host_overclaims_fail(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    artifact = tmp_path / mod.ARTIFACT_PATH  # type: ignore[attr-defined]
    artifact.write_text(
        artifact.read_text(encoding="utf-8")
        + "\nStory 132.5 operator/dashboard split is production-ready and deployed.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("overclaim" in v.message for v in violations)


def test_console_cli_not_compose_evidence_required(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    digest = tmp_path / mod.DIGEST_COMPOSE_PATH  # type: ignore[attr-defined]
    digest.write_text(
        digest.read_text(encoding="utf-8").replace(
            "console-cli — not compose services", "console-cli service"
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("console-cli non-compose evidence" in v.message for v in violations)


def test_wiring_required_in_just_and_ci(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    for rel in [mod.JUSTFILE_PATH, mod.CI_PATH]:  # type: ignore[attr-defined]
        target = tmp_path / rel
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                mod.CHECKER_COMMAND, "uv run python scripts/other.py"
            ),
            encoding="utf-8",
        )  # type: ignore[attr-defined]
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("missing required reference" in v.message for v in violations)
