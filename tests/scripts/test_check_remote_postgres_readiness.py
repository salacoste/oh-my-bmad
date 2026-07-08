"""Tests for Story 132.2 remote Postgres readiness gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_remote_postgres_readiness.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_remote_postgres_readiness", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_remote_postgres_readiness"] = mod
    spec.loader.exec_module(mod)
    return mod


def _copy_live_fixture(tmp_path: Path, mod: object) -> None:
    for rel in [
        mod.CONTRACT_PATH,  # type: ignore[attr-defined]
        mod.OPERATOR_RUNBOOK_PATH,  # type: ignore[attr-defined]
        mod.PRODUCTION_OPS_PATH,  # type: ignore[attr-defined]
        mod.FEATURE_STATUS_PATH,  # type: ignore[attr-defined]
        mod.BACKUP_RESTORE_PATH,  # type: ignore[attr-defined]
        mod.SPRINT_STATUS_PATH,  # type: ignore[attr-defined]
        mod.ARTIFACT_PATH,  # type: ignore[attr-defined]
        mod.JUSTFILE_PATH,  # type: ignore[attr-defined]
        mod.CI_PATH,  # type: ignore[attr-defined]
        mod.REGISTRY_STATE_ENGINE_PATH,  # type: ignore[attr-defined]
        mod.REGISTRY_STATE_MIGRATIONS_PATH,  # type: ignore[attr-defined]
        mod.REGISTRY_API_APP_PATH,  # type: ignore[attr-defined]
        mod.MTLS_RUNTIME_PATH,  # type: ignore[attr-defined]
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


def test_missing_required_section_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data.pop("bounded_pool_contract")
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("required remote Postgres sections missing" in v.message for v in violations)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("pool_size_formula", "unbounded", "pool_size_formula"),
        ("max_overflow", 50, "max_overflow"),
        ("pool_timeout_seconds", 3, "pool_timeout_seconds"),
        ("pool_recycle_seconds", 60, "pool_recycle_seconds"),
        ("pool_pre_ping", False, "pool_pre_ping"),
    ],
)
def test_bounded_pool_contract_exact_values_fail(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    pool = data["bounded_pool_contract"]
    assert isinstance(pool, dict)
    pool[field] = value
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(expected in v.message for v in violations)


def test_sqlite_default_preservation_false_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    current = data["current_default_preservation"]
    assert isinstance(current, dict)
    current["sqlite_default"] = False
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("sqlite_default=true" in v.message for v in violations)


def test_migration_backup_evidence_missing_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    migration = data["migration_and_backup_gate"]
    assert isinstance(migration, dict)
    migration["required_evidence"] = ["single migration runner identity and lock/election evidence"]
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("migration evidence missing" in v.message for v in violations)


def test_backup_restore_checksum_integrity_and_rollback_missing_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    drill = data["backup_restore_drill"]
    assert isinstance(drill, dict)
    drill["required_evidence"] = []
    drill["integrity_checks"] = []
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("backup/restore drill evidence missing" in v.message for v in violations)
    assert any("checksum" in v.message for v in violations)
    assert any("integrity" in v.message for v in violations)
    assert any("rollback/fix-forward" in v.message for v in violations)


def test_db_mtls_composition_missing_gate_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    mtls = data["db_mtls_composition"]
    assert isinstance(mtls, dict)
    mtls["env_key"] = "OTHER_FLAG"
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("REGISTRY_DB_MTLS_ENABLED" in v.message for v in violations)


@pytest.mark.parametrize(
    ("rel_attr", "replacement", "expected"),
    [
        ("OPERATOR_RUNBOOK_PATH", "uv run python scripts/other.py", "missing required reference"),
        (
            "JUSTFILE_PATH",
            "uv run python scripts/other.py",
            "missing remote Postgres readiness checker",
        ),
        (
            "CI_PATH",
            "uv run python scripts/other.py",
            "CI missing remote Postgres readiness checker",
        ),
    ],
)
def test_docs_just_ci_wiring_fails(
    tmp_path: Path, rel_attr: str, replacement: str, expected: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    rel = getattr(mod, rel_attr)
    target = tmp_path / rel
    target.write_text(
        target.read_text(encoding="utf-8").replace(mod.CHECKER_COMMAND, replacement),  # type: ignore[attr-defined]
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(expected in v.message for v in violations)


def test_secret_like_material_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["bad"] = "postgresql+asyncpg://user:realcredentialvalue@db.example.invalid/app"
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)


def test_remote_postgres_overclaim_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\nRemote Postgres production is live for customers.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("overclaim" in v.message for v in violations)


def test_runtime_string_guard_fails_when_pool_formula_removed(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.REGISTRY_STATE_ENGINE_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "pool_size = 5 + 2 * safe_worker_count", "pool_size = safe_worker_count"
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("pool_size = 5 + 2 * safe_worker_count" in v.message for v in violations)
