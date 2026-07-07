"""Tests for Story 132.1 split deployment topology readiness gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_split_deployment_topology.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_split_deployment_topology", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_split_deployment_topology"] = mod
    spec.loader.exec_module(mod)
    return mod


def _copy_live_fixture(tmp_path: Path, mod: object) -> None:
    for rel in [
        mod.CONTRACT_PATH,  # type: ignore[attr-defined]
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
    return json.loads((tmp_path / mod.CONTRACT_PATH).read_text(encoding="utf-8"))  # type: ignore[attr-defined]


def _write_contract(tmp_path: Path, mod: object, data: dict[str, object]) -> None:
    (tmp_path / mod.CONTRACT_PATH).write_text(json.dumps(data, indent=2), encoding="utf-8")  # type: ignore[attr-defined]


def test_self_test_passes() -> None:
    mod = _load_module()
    assert mod._self_test() == 0  # type: ignore[attr-defined]


def test_live_contract_is_clean() -> None:
    mod = _load_module()
    assert mod.main([]) == 0  # type: ignore[attr-defined]


def test_missing_current_default_preservation_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data.pop("current_default_preservation")
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("required topology sections missing" in v.message for v in violations)


@pytest.mark.parametrize(
    "flag",
    [
        "no_compose_profile_change",
        "no_env_activation_flag",
        "no_remote_postgres_connection_code",
    ],
)
def test_current_default_preservation_false_flags_fail(tmp_path: Path, flag: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    current_default = data["current_default_preservation"]
    assert isinstance(current_default, dict)
    current_default[flag] = False
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(
        "current_default_preservation" in v.message and flag in v.message for v in violations
    )


def test_missing_service_placement_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["service_placement"] = {"required_services": ["registry_api"]}
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(
        "service_placement missing" in v.message and "clawhip_daemon" in v.message
        for v in violations
    )


@pytest.mark.parametrize(
    ("section", "bad_status"),
    [
        ("current_default_preservation", "implemented"),
        ("current_default_preservation", "active"),
        ("service_placement", "implemented"),
        ("service_placement", "active"),
        ("network_boundaries", "implemented"),
        ("network_boundaries", "active"),
        ("remote_postgres_data_authority", "implemented"),
        ("remote_postgres_data_authority", "active"),
        ("pooling_migration_backup_prerequisites", "implemented"),
        ("pooling_migration_backup_prerequisites", "active"),
        ("rollback_fallback", "implemented"),
        ("rollback_fallback", "active"),
    ],
)
def test_guarded_contract_section_implemented_or_active_status_fails(
    tmp_path: Path, section: str, bad_status: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    section_data = data[section]
    assert isinstance(section_data, dict)
    section_data["status"] = bad_status
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(f"{section} status must be" in v.message for v in violations)


def test_missing_network_boundary_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["network_boundaries"] = {"required_boundaries": ["public ingress only"]}
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("network_boundaries" in v.message for v in violations)


@pytest.mark.parametrize(
    ("section", "replacement", "expected"),
    [
        ("ingress", {}, "ingress"),
        (
            "ingress",
            {"status": "implemented", "requirements": ["public ingress routes directly anywhere"]},
            "ingress status must be contract_only",
        ),
        ("secrets_handling", {}, "secrets_handling"),
        (
            "secrets_handling",
            {
                "status": "implemented",
                "requirements": ["remote database passwords may be committed"],
            },
            "secrets_handling status must be metadata_only",
        ),
        ("observability", {}, "observability"),
        (
            "observability",
            {"status": "already_available", "requirements": ["generic uptime logs are enough"]},
            "observability status must be future_required_evidence",
        ),
        ("fail_closed_checks", [], "fail_closed_checks missing"),
        (
            "fail_closed_checks",
            ["activation checks are advisory after deployment"],
            "fail_closed_checks missing",
        ),
    ],
)
def test_required_split_topology_sections_empty_or_contradictory_fail(
    tmp_path: Path, section: str, replacement: object, expected: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data[section] = replacement
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(expected in v.message for v in violations)


def test_missing_remote_postgres_authority_and_data_boundary_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["remote_postgres_data_authority"] = {
        "status": "deferred_fail_closed",
        "authority_rules": ["remote database later"],
        "data_boundary_coverage": ["state mutation authority"],
    }
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("remote_postgres_data_authority missing" in v.message for v in violations)
    assert any("data-boundary coverage missing" in v.message for v in violations)


def test_missing_unsupported_topology_fail_closed_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["unsupported_topologies"] = {"status": "advisory", "entries": []}
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("unsupported_topologies" in v.message for v in violations)


def test_missing_mtls_deferment_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["db_mtls_deferment"] = {"status": "implemented", "epic": "132"}
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("db_mtls_deferment" in v.message for v in violations)


@pytest.mark.parametrize(
    "missing_key",
    [
        "single_writer_state_mutation",
        "append_only_event_log_authority",
        "idempotency_and_locking",
        "capability_tiers",
    ],
)
def test_missing_core_invariant_fails(tmp_path: Path, missing_key: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    invariants = data["core_invariants"]
    assert isinstance(invariants, dict)
    invariants.pop(missing_key)
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("core_invariants missing" in v.message for v in violations)


def test_active_topology_overclaim_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    feature_status = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    feature_status.write_text(
        feature_status.read_text(encoding="utf-8")
        + "\nStory 132.1 says split deployment is enabled.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("overclaim" in v.message for v in violations)


def test_secret_like_value_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["example_secret"] = "postgres://app:supersecretpassword@example.invalid:5432/app"
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("credential" in v.message or "secret-like" in v.message for v in violations)


def test_missing_docs_status_references_fail(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["docs_refs"] = ["docs/operator-runbook.md"]
    data["status_refs"] = []
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("docs_refs missing" in v.message for v in violations)
    assert any("status_refs missing" in v.message for v in violations)


def test_stale_docs_anchor_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    docs_refs = data["docs_refs"]
    assert isinstance(docs_refs, list)
    docs_refs[0] = "docs/operator-runbook.md#stale-split-deployment-anchor"
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("referenced markdown anchor does not exist" in v.message for v in violations)


def test_missing_mandatory_justfile_and_ci_wiring_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    justfile = tmp_path / mod.JUSTFILE_PATH  # type: ignore[attr-defined]
    ci = tmp_path / mod.CI_PATH  # type: ignore[attr-defined]
    justfile.write_text(
        justfile.read_text(encoding="utf-8").replace(mod.CHECKER_COMMAND, "", 1),  # type: ignore[attr-defined]
        encoding="utf-8",
    )
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(mod.CHECKER_SELF_TEST_COMMAND, "", 1),  # type: ignore[attr-defined]
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("justfile wiring" in v.message or "lint:" in v.message for v in violations)
    assert any("CI missing" in v.message for v in violations)


def test_ci_missing_normal_checker_step_fails_even_with_self_test_present(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    ci = tmp_path / mod.CI_PATH  # type: ignore[attr-defined]
    ci.write_text(
        ci.read_text(encoding="utf-8").replace(
            "      - name: Check split deployment topology readiness (Story 132.1)\n"
            "        run: uv run python scripts/check_split_deployment_topology.py\n\n",
            "",
            1,
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("CI missing split topology checker step" in v.message for v in violations)
    assert not any("CI missing split topology checker self-test" in v.message for v in violations)


@pytest.mark.parametrize(
    ("relpath", "content", "expected"),
    [
        ("docker-compose.split.yml", "services: {} # split deployment", "compose profile/overlay"),
        ("compose.yaml", "services: {} # split deployment", "compose profile/overlay"),
        (".env.production", "SPLIT_DEPLOYMENT_ENABLED=true", "environment activation"),
        ("pyproject.toml", "REMOTE_POSTGRES_ENABLED=true", "environment activation"),
        ("justfile", "deploy-split:\n    echo nope\n", "deploy target"),
        ("scripts/run_remote_pg.py", "remote_postgres_migration_runner = True", "migration runner"),
        ("services/registry-api/routes.py", "'/remote-postgres/enable'", "service route"),
        ("Dockerfile.split", "ENV REMOTE_POSTGRES=1", "Dockerfile activation"),
        (
            "packages/replay/src/replay/db.py",
            "REMOTE_POSTGRES_URL = 'metadata-only'",
            "connection code",
        ),
        ("justfile", "ssh ops@remote-postgres.example.invalid true", "external host"),
    ],
)
def test_forbidden_runtime_expansion_surfaces_fail(
    tmp_path: Path, relpath: str, content: str, expected: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        content = target.read_text(encoding="utf-8") + "\n" + content
    target.write_text(content, encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(expected in v.message for v in violations)
