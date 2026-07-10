"""Tests for Story 132.4 worker/MCP/event-bus split readiness gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_worker_mcp_event_bus_split.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_worker_mcp_event_bus_split", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_worker_mcp_event_bus_split"] = mod
    spec.loader.exec_module(mod)
    return mod


def _copy_live_fixture(tmp_path: Path, mod: object) -> None:
    for rel in [
        mod.CONTRACT_PATH,  # type: ignore[attr-defined]
        mod.OVERLAY_PATH,  # type: ignore[attr-defined]
        mod.ROOT_COMPOSE_PATH,  # type: ignore[attr-defined]
        mod.ENV_EXAMPLE_PATH,  # type: ignore[attr-defined]
        mod.OPERATOR_RUNBOOK_PATH,  # type: ignore[attr-defined]
        mod.PRODUCTION_OPS_PATH,  # type: ignore[attr-defined]
        mod.FEATURE_STATUS_PATH,  # type: ignore[attr-defined]
        mod.SPRINT_STATUS_PATH,  # type: ignore[attr-defined]
        mod.ARTIFACT_PATH,  # type: ignore[attr-defined]
        mod.JUSTFILE_PATH,  # type: ignore[attr-defined]
        mod.CI_PATH,  # type: ignore[attr-defined]
    ]:
        src = REPO_ROOT / rel
        dst = tmp_path / rel
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


def test_overlay_must_require_mcp_auth_token(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    overlay = tmp_path / mod.OVERLAY_PATH  # type: ignore[attr-defined]
    overlay.write_text(
        overlay.read_text(encoding="utf-8").replace("${MCP_AUTH_TOKEN:?", "${MCP_AUTH_TOKEN:-"),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("MCP_AUTH_TOKEN" in v.message for v in violations)


def test_overlay_must_require_jwt_secret_key(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    overlay = tmp_path / mod.OVERLAY_PATH  # type: ignore[attr-defined]
    overlay.write_text(
        overlay.read_text(encoding="utf-8").replace("${JWT_SECRET_KEY:?", "${JWT_SECRET_KEY:-"),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("JWT_SECRET_KEY" in v.message for v in violations)


def test_contract_must_list_all_mcp_urls(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    wiring = data["spawner_remote_mcp_wiring"]
    assert isinstance(wiring, dict)
    wiring["required_urls"] = ["TASK_REGISTRY_URL"]
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("required_urls missing" in v.message for v in violations)


def test_event_bus_writer_must_be_remote_clawhip_bridge(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    event_bus = data["event_bus_boundary"]
    assert isinstance(event_bus, dict)
    event_bus["writer_service"] = "worker-wrapper"
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("writer_service" in v.message for v in violations)


def test_host_ports_forbidden(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    overlay = tmp_path / mod.OVERLAY_PATH  # type: ignore[attr-defined]
    overlay.write_text(
        overlay.read_text(encoding="utf-8") + "\nports:\n  - 8081:8081\n", encoding="utf-8"
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("host ports" in v.message for v in violations)


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
