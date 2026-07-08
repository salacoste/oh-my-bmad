"""Tests for Story 132.3 registry remote Postgres profile readiness gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_registry_remote_postgres_profile.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_registry_remote_postgres_profile", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_registry_remote_postgres_profile"] = mod
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


def test_missing_required_contract_section_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data.pop("registry_service_wiring")
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("required 132.3 sections missing" in v.message for v in violations)


def test_overlay_must_require_registry_database_url(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    overlay = tmp_path / mod.OVERLAY_PATH  # type: ignore[attr-defined]
    overlay.write_text(
        overlay.read_text(encoding="utf-8").replace("${REGISTRY_DATABASE_URL:?", "${OTHER_URL:?"),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("REGISTRY_DATABASE_URL" in v.message for v in violations)


def test_root_compose_must_not_set_shared_database_url(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    root_compose = tmp_path / mod.ROOT_COMPOSE_PATH  # type: ignore[attr-defined]
    root_compose.write_text(
        root_compose.read_text(encoding="utf-8") + "\n# violation\nREGISTRY_DATABASE_URL: value\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("root compose must not set REGISTRY_DATABASE_URL" in v.message for v in violations)


def test_env_example_registry_database_url_must_be_blank(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    env_example = tmp_path / mod.ENV_EXAMPLE_PATH  # type: ignore[attr-defined]
    env_example.write_text(
        env_example.read_text(encoding="utf-8").replace(
            "REGISTRY_DATABASE_URL=",
            "REGISTRY_DATABASE_URL=postgresql+asyncpg://app:password@example.invalid/db",
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("REGISTRY_DATABASE_URL must be blank" in v.message for v in violations)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("idempotency_uses_same_remote_dsn", False, "idempotency_uses_same_remote_dsn"),
        ("registry_api_not_migration_runner", False, "registry_api_not_migration_runner"),
    ],
)
def test_registry_wiring_required_booleans_fail(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    wiring = data["registry_service_wiring"]
    assert isinstance(wiring, dict)
    wiring[field] = value
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(expected in v.message for v in violations)


def test_startup_schema_creation_must_be_disabled(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    migration = data["migration_strategy"]
    assert isinstance(migration, dict)
    migration["startup_schema_creation_disabled"] = False
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("startup_schema_creation_disabled" in v.message for v in violations)


def test_db_mtls_enabled_key_required(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    mtls = data["db_mtls_policy_composition"]
    assert isinstance(mtls, dict)
    mtls["enabled_key"] = "OTHER_FLAG"
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("REGISTRY_DB_MTLS_ENABLED" in v.message for v in violations)


def test_just_and_ci_wiring_required(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    for rel in [mod.JUSTFILE_PATH, mod.CI_PATH]:  # type: ignore[attr-defined]
        target = tmp_path / rel
        target.write_text(
            target.read_text(encoding="utf-8").replace(mod.CHECKER_COMMAND, "uv run python scripts/other.py"),  # type: ignore[attr-defined]
            encoding="utf-8",
        )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("checker" in v.message for v in violations)
