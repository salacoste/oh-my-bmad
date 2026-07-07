"""Tests for Story 132.1 split deployment topology readiness gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import cast

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
    raw: object = json.loads((tmp_path / mod.CONTRACT_PATH).read_text(encoding="utf-8"))  # type: ignore[attr-defined]
    assert isinstance(raw, dict)
    return cast("dict[str, object]", raw)


def _set_nested_value(data: dict[str, object], path: tuple[str, ...], value: object) -> None:
    current: dict[str, object] = data
    for key in path[:-1]:
        child = current[key]
        assert isinstance(child, dict)
        current = cast("dict[str, object]", child)
    current[path[-1]] = value


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


@pytest.mark.parametrize(
    ("path", "bad_status"),
    [
        (("mode",), "rollout_complete"),
        (("production_activation",), "production ready"),
        (("current_default_preservation", "status"), "activated"),
        (("service_placement", "status"), "available_now"),
        (("network_boundaries", "status"), "shipped"),
    ],
)
def test_machine_readable_activation_statuses_fail(
    tmp_path: Path, path: tuple[str, ...], bad_status: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    _set_nested_value(data, path, bad_status)
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("machine-readable status must be" in v.message for v in violations)
    assert any("must not claim activation status" in v.message for v in violations)


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


def test_active_topology_overclaim_variant_without_is_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    feature_status = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    feature_status.write_text(
        feature_status.read_text(encoding="utf-8")
        + "\nSplit deployment enabled for production and remote Postgres available now.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("overclaim" in v.message for v in violations)


def test_active_topology_overclaim_with_interposed_words_and_has_been_fails(
    tmp_path: Path,
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    feature_status = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    feature_status.write_text(
        feature_status.read_text(encoding="utf-8")
        + "\nRemote Postgres is now live for production. Split deployment has been implemented.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "claim",
    [
        "Split deployment rollout complete.",
        "Remote Postgres production ready.",
        "Remote Postgres available now.",
        "Split deployment has been activated.",
        "Remote Postgres has been shipped.",
        "Production-ready remote Postgres support.",
        "Enabled split deployment support.",
        "No gate is needed because split deployment is enabled.",
    ],
)
def test_active_topology_overclaim_broadened_variants_fail(tmp_path: Path, claim: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    feature_status = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    feature_status.write_text(
        f"{feature_status.read_text(encoding='utf-8')}\n{claim}\n",
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


@pytest.mark.parametrize(
    "secret_url",
    [
        "postgresql+asyncpg://app:supersecretpassword@example.invalid:5432/app",
        "postgresql+psycopg://app:supersecretpassword@example.invalid:5432/app",
        "postgresql+asyncpg://app:supersecretpassword@localhost:5432/app",
    ],
)
def test_driver_qualified_postgres_secret_like_value_fails(tmp_path: Path, secret_url: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["example_secret"] = secret_url
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


def test_commented_out_justfile_wiring_is_not_executable(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    justfile = tmp_path / mod.JUSTFILE_PATH  # type: ignore[attr-defined]
    text = justfile.read_text(encoding="utf-8")
    text = text.replace(
        f"    {mod.CHECKER_COMMAND}\n",  # type: ignore[attr-defined]
        f"    # {mod.CHECKER_COMMAND}\n",  # type: ignore[attr-defined]
    )
    text = text.replace(
        f"    {mod.CHECKER_SELF_TEST_COMMAND}\n",  # type: ignore[attr-defined]
        f"    # {mod.CHECKER_SELF_TEST_COMMAND}\n",  # type: ignore[attr-defined]
    )
    justfile.write_text(text, encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("lint:" in v.message for v in violations)
    assert any("check-gates:" in v.message for v in violations)
    assert any("check-gates-self-test" in v.message for v in violations)


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
        ("compose.split.yml", "services: {}\n", "compose profile/overlay"),
        (
            "compose.split.yml",
            "services:\n  db:\n    image: postgres:16\n    profiles: [split]\n",
            "compose profile/overlay",
        ),
        (
            "deployments/remote-postgres.yml",
            "services:\n  db:\n    image: postgres:16\n",
            "compose profile/overlay",
        ),
        (
            "deployments/remote-postgres.yml",
            "services:\n  api:\n    build: .\n",
            "compose profile/overlay",
        ),
        (
            "deployments/remote-postgres.yml",
            "services: {api: {build: .}}\n",
            "compose profile/overlay",
        ),
        (
            "deployments/remote-postgres/compose.yml",
            "services:\n  api:\n    build: .\n",
            "compose profile/overlay",
        ),
        (
            "deployments/split/docker-compose.yml",
            "services:\n  api:\n    build: .\n",
            "compose profile/overlay",
        ),
        (
            "ops/split-deployment/compose.yaml",
            "services:\n  api:\n    image: registry-api:latest\n",
            "compose profile/overlay",
        ),
        (
            "infra/remote_postgres/docker-compose.yaml",
            "services:\n  db:\n    image: postgres:16\n",
            "compose profile/overlay",
        ),
        (
            "ops/split-deployment.yaml",
            "services:\n  api:\n    image: registry-api:latest\n",
            "compose profile/overlay",
        ),
        ("compose.yaml", "services: {} # split deployment", "compose profile/overlay"),
        (
            "ops/app.yaml",
            "services:\n  api:\n    image: registry-api:latest\n    profiles: [split]\n",
            "compose profile/overlay",
        ),
        (".env.production", "SPLIT_DEPLOYMENT_ENABLED=true", "environment activation"),
        (".env.production", "ENABLE_SPLIT_DEPLOYMENT=true", "environment activation"),
        ("pyproject.toml", "SPLIT_DEPLOYMENT=true", "environment activation"),
        ("pyproject.toml", "REMOTE_POSTGRES_ENABLED=true", "environment activation"),
        ("pyproject.toml", "REMOTE_POSTGRES=true", "environment activation"),
        ("justfile", "deploy-split:\n    echo nope\n", "deploy target"),
        ("scripts/run_remote_pg.py", "remote_postgres_migration_runner = True", "migration runner"),
        ("services/registry-api/routes.py", "'/remote-postgres/enable'", "service route"),
        ("Dockerfile.split", "ENV REMOTE_POSTGRES=1", "Dockerfile activation"),
        (
            "packages/replay/src/replay/db.py",
            "REMOTE_POSTGRES_URL = 'metadata-only'",
            "connection code",
        ),
        (
            "packages/replay/src/replay/db.py",
            "DATABASE_URL = 'postgres://remote-postgres.example.invalid/app'",
            "connection code",
        ),
        (
            "packages/replay/src/replay/db.py",
            "DATABASE_URL = 'postgres://prod-db.example.invalid/app'",
            "connection code",
        ),
        (
            "packages/replay/src/replay/db.py",
            "DATABASE_URL = 'postgresql+asyncpg://prod-db.example.invalid/app'",
            "connection code",
        ),
        (
            "packages/replay/src/replay/db.py",
            'DATABASE_URL = "postgresql+asyncpg://app:supersecretpassword@localhost:5432/app"',
            "secret-like",
        ),
        (
            "config/runtime.json",
            '{"DATABASE_URL":"postgresql+asyncpg://app:supersecretpassword@localhost:5432/app"}',
            "secret-like",
        ),
        (
            "config/runtime.json",
            '{"DATABASE_URL":"postgres://app:hunter2@localhost:5432/app"}',
            "secret-like",
        ),
        (
            "packages/replay/src/replay/db.py",
            'os.environ["DATABASE_URL"] = "postgres://prod-db.example.invalid/app"',
            "connection code",
        ),
        (
            "config/runtime.json",
            '{"DATABASE_URL":"postgres://prod-db.example.invalid/app"}',
            "connection code",
        ),
        (
            "deployments/app.yaml",
            "env:\n  - name: DATABASE_URL\n    value: postgres://prod-db.example.invalid/app\n",
            "connection code",
        ),
        (
            "deployments/app.yaml",
            "env:\n  - value: postgres://prod-db.example.invalid/app\n    name: DATABASE_URL\n",
            "connection code",
        ),
        (".env.production", "DATABASE_URL=postgres://192.0.2.10/app", "connection code"),
        (
            ".env.production",
            "POSTGRES_DSN=postgres://remote-postgres.example.invalid/app",
            "connection code",
        ),
        (
            ".env.production",
            "POSTGRES_DSN=postgresql+psycopg://prod-db.example.invalid/app",
            "connection code",
        ),
        (".env.production", "POSTGRES_HOST=remote-postgres.example.invalid", "connection code"),
        (".env.production", "POSTGRES_HOST=prod-db.example.invalid", "connection code"),
        (".env.production", "POSTGRES_HOST=localhost.example.invalid", "connection code"),
        (".env.production", "POSTGRES_HOST=127.0.0.1.example.invalid", "connection code"),
        (".env.production", "POSTGRES_HOST=192.0.2.10", "connection code"),
        (".env.production", "PGHOST=remote-postgres.example.invalid", "connection code"),
        (".env.production", "PGHOST=prod-db.example.invalid", "connection code"),
        (".env.production", "PGHOST=localhost.example.invalid", "connection code"),
        (".env.production", "PGHOST=127.0.0.1.example.invalid", "connection code"),
        ("config/runtime.json", '{"PGHOST": "prod-db.example.invalid"}', "connection code"),
        (
            ".env.production",
            "POSTGRES_URL=postgres://remote-postgres.example.invalid/app",
            "connection code",
        ),
        (
            ".env.production",
            "POSTGRES_URL=postgres://prod-db.example.invalid/app",
            "connection code",
        ),
        (
            ".env.production",
            "POSTGRES_URL=postgresql+asyncpg://prod-db.example.invalid/app",
            "connection code",
        ),
        (
            "config/runtime.toml",
            '"POSTGRES_URL" = "postgres://prod-db.example.invalid/app"',
            "connection code",
        ),
        (
            ".env.production",
            "REMOTE_PG_DSN=postgres://remote-postgres.example.invalid/app",
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


@pytest.mark.parametrize(
    ("relpath", "content"),
    [
        (".env.production", "DATABASE_URL=postgres://localhost:5432/app\n"),
        (".env.production", "DATABASE_URL=postgres://127.0.0.1:5432/app\n"),
        (".env.production", "DATABASE_URL=postgres://[::1]:5432/app\n"),
        (".env.production", "POSTGRES_HOST=localhost\n"),
        (".env.production", 'PGHOST="localhost"\n'),
        ("config/runtime.json", '{"PGHOST":"localhost"}'),
        (".env.production", "POSTGRES_HOST=127.0.0.1\n"),
        (".env.production", "POSTGRES_HOST=localhost:5432\n"),
        (".env.production", "POSTGRES_HOST=127.0.0.1:5432\n"),
        (".env.production", "POSTGRES_HOST=[::1]\n"),
        ("config/runtime.json", '{"DATABASE_URL":"postgres://localhost:5432/app"}'),
        ("config/runtime.json", '{"DATABASE_URL":"postgres://localhost"}'),
        (".env.production", "DATABASE_URL=postgres://localhost?sslmode=disable\n"),
        ("config/runtime.json", '{"DATABASE_URL":"postgres://localhost?sslmode=disable"}'),
    ],
)
def test_local_postgres_dsn_and_host_values_do_not_false_positive(
    tmp_path: Path, relpath: str, content: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert not any("connection code" in v.message for v in violations)
