"""Tests for Epic 133 DB mTLS readiness gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_db_mtls_readiness.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_db_mtls_readiness", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_db_mtls_readiness"] = mod
    spec.loader.exec_module(mod)
    return mod


def _copy_live_fixture(tmp_path: Path, mod: object) -> None:
    for rel in [
        mod.CONTRACT_PATH,  # type: ignore[attr-defined]
        mod.OPERATOR_RUNBOOK_PATH,  # type: ignore[attr-defined]
        mod.PRODUCTION_OPS_PATH,  # type: ignore[attr-defined]
        mod.FEATURE_STATUS_PATH,  # type: ignore[attr-defined]
        mod.SPRINT_STATUS_PATH,  # type: ignore[attr-defined]
        mod.ARTIFACT_133_1_PATH,  # type: ignore[attr-defined]
        mod.CLOSURE_ARTIFACT_PATH,  # type: ignore[attr-defined]
        mod.JUSTFILE_PATH,  # type: ignore[attr-defined]
        mod.CI_PATH,  # type: ignore[attr-defined]
        mod.ARCHITECT_APPROVAL_PATH,  # type: ignore[attr-defined]
        mod.CRITIC_APPROVAL_PATH,  # type: ignore[attr-defined]
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
    data.pop("server_side_postgres_evidence")
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("required DB mTLS sections missing" in v.message for v in violations)


@pytest.mark.parametrize("rel_attr", ["OPERATOR_RUNBOOK_PATH", "JUSTFILE_PATH", "CI_PATH"])
def test_missing_docs_just_ci_wiring_fails(tmp_path: Path, rel_attr: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    rel = getattr(mod, rel_attr)
    path = tmp_path / rel
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(mod.CHECKER_COMMAND, "uv run python scripts/other.py"), encoding="utf-8"
    )  # type: ignore[attr-defined]
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert violations


def test_secret_like_material_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["bad"] = "-----BEGIN " + "PRIVATE KEY-----"
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DB mTLS is live for production.", "overclaim"),
        ("plaintext fallback is allowed after failure.", "plaintext fallback"),
    ],
)
def test_overclaim_and_plaintext_fallback_fail(tmp_path: Path, text: str, expected: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + "\n" + text, encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(expected in v.message for v in violations)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("postgresql_conf_required", ["ssl_cert_file"], "server-side Postgres settings missing"),
        ("application_database", "otherdb", "application_database=registry"),
        ("application_role", "otherrole", "application_role=app"),
        ("revocation_settings_required", [], "CRL setting required"),
        ("approved_secret_evidence", [], "approved secret evidence missing"),
        ("pg_hba_required", ["hostssl"], "hostssl clientcert evidence missing"),
        ("sslmode_disable_rejection", False, "sslmode=disable rejection"),
    ],
)
def test_missing_server_side_postgres_requirements_fail(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    server = data["server_side_postgres_evidence"]
    assert isinstance(server, dict)
    server[field] = value
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(expected in v.message for v in violations)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("approved_prefixes", ["/run/secrets/"], "approved_secret_locations missing"),
        ("canonical_resolution", "lexical_prefix_only", "canonical realpath"),
        ("symlink_escape_policy", "allow", "symlink/canonical path escapes"),
        ("example_paths", ["/tmp/not-approved/material"], "unapproved path example"),
    ],
)
def test_approved_prefix_and_canonical_policy_fail(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    approved = data["approved_secret_locations"]
    assert isinstance(approved, dict)
    approved[field] = value
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(expected in v.message for v in violations)


def test_missing_rotation_revocation_drills_fail(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    rotation = data["rotation_revocation_drills"]
    assert isinstance(rotation, dict)
    rotation["required_evidence"] = ["replacement_server_cert_from_approved_ca"]
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("rotation/revocation drill missing" in v.message for v in violations)


def test_missing_failure_classes_and_bounded_retry_fail(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    failure = data["failure_observability"]
    assert isinstance(failure, dict)
    failure["failure_classes"] = ["invalid_ca"]
    failure["bounded_retry"] = {"required": True, "outcome": "retry_forever"}
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("classes missing" in v.message for v in violations)
    assert any("bounded retry" in v.message for v in violations)


def test_closure_artifact_required_entries_fail(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.CLOSURE_ARTIFACT_PATH  # type: ignore[attr-defined]
    target.write_text("# Story 133.5\n\ncode-review\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("closure evidence missing" in v.message for v in violations)


def test_closure_artifact_pending_placeholder_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.CLOSURE_ARTIFACT_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8") + "\n- code-review: pending until gates run.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("pending-until-gates placeholders" in v.message for v in violations)


@pytest.mark.parametrize("status", ["in-progress", "backlog"])
def test_sprint_story_133_5_open_status_fails(tmp_path: Path, status: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    text = target.read_text(encoding="utf-8")
    text = text.replace(
        "133-5-db-mtls-closure-evidence: done", f"133-5-db-mtls-closure-evidence: {status}"
    )
    target.write_text(text, encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(
        "133.5 DB mTLS closure evidence must be done/closed" in v.message for v in violations
    )


def test_feature_status_postgres_mtls_deferred_not_implemented_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    text = target.read_text(encoding="utf-8")
    text = text.replace(
        "| Postgres connection mTLS | Runtime-gated implemented; production activation deferred | "
        "Registry-state DB mTLS support is implemented behind `REGISTRY_DB_MTLS_ENABLED` with fail-closed URL, secret-path, rotation/revocation, failure-observability, and checker coverage. Production activation remains deferred until operator server evidence and approved secrets are supplied. |",
        "| Postgres connection mTLS | Deferred / not implemented | Internal Docker-network mTLS exists; database connection mTLS remains future work. |",
    )
    target.write_text(text, encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("deferred/not implemented" in v.message for v in violations)


def test_feature_status_stale_db_mtls_planning_only_bullet_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\n- DB connection mTLS remain planning-only/deferred until their implementation "
        "stories are executed and verified.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("planning-only/deferred" in v.message for v in violations)


def test_feature_status_stale_current_phase_epic_130_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    text = target.read_text(encoding="utf-8")
    stale_phase = (
        "- **Current phase:** Phase 48 / Epic 130 is locally closed as the current "
        "retention/object-storage lifecycle readiness track after Epic 131 readiness closure."
    )
    lines = [
        stale_phase if line.startswith("- **Current phase:**") else line
        for line in text.splitlines()
    ]
    assert stale_phase in lines
    text = "\n".join(lines) + "\n"
    target.write_text(text, encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("Phase 50 / Epic 133" in v.message for v in violations)


def test_runtime_code_rejects_static_only_or_backlog_status_claims(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    runtime = tmp_path / mod.MTLS_RUNTIME_PATH  # type: ignore[attr-defined]
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text("# runtime mTLS builder exists\n", encoding="utf-8")
    registry_runtime = tmp_path / mod.REGISTRY_RUNTIME_PATH  # type: ignore[attr-defined]
    registry_runtime.parent.mkdir(parents=True, exist_ok=True)
    registry_runtime.write_text("# registry runtime wiring exists\n", encoding="utf-8")
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8") + "\nEpic 133 does not add runtime DB mTLS code.",
        encoding="utf-8",
    )
    sprint = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    sprint.write_text(
        sprint.read_text(encoding="utf-8")
        + "\n  133-2-postgres-server-client-mtls-runtime: backlog\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("runtime-gated support" in v.message for v in violations)


@pytest.mark.parametrize(
    "stale_phrase",
    [
        "Story 133.1 defines the DB mTLS readiness contract as static evidence only.",
        "DB mTLS expiry freshness refreshes on the runtime activation story.",
    ],
)
def test_sprint_status_rejects_stale_static_only_db_mtls_phrasing(
    tmp_path: Path, stale_phrase: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    runtime = tmp_path / mod.MTLS_RUNTIME_PATH  # type: ignore[attr-defined]
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text("# runtime mTLS builder exists\n", encoding="utf-8")
    registry_runtime = tmp_path / mod.REGISTRY_RUNTIME_PATH  # type: ignore[attr-defined]
    registry_runtime.parent.mkdir(parents=True, exist_ok=True)
    registry_runtime.write_text("# registry runtime wiring exists\n", encoding="utf-8")
    sprint = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    sprint.write_text(
        sprint.read_text(encoding="utf-8") + "\n" + stale_phrase + "\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("runtime-gated support" in v.message for v in violations)


def test_violation_render_redacts_sensitive_output() -> None:
    mod = _load_module()
    secret = "real" + "password" + "value"
    pem = "-----BEGIN " + "CERTIFICATE-----x-----END " + "CERTIFICATE-----"
    rendered = mod.Violation(  # type: ignore[attr-defined]
        "docs/db-mtls-readiness.json",
        f"postgresql+asyncpg://user:{secret}@prod-db.internal:5432/app "
        f"/run/secrets/db/client.key CN=prod SAN=prod {pem}",
    ).render()
    assert secret not in rendered
    assert "client.key" not in rendered
    assert "/run/secrets/db" not in rendered
    assert "BEGIN CERTIFICATE" not in rendered
    assert "CN=prod" not in rendered
    assert "SAN=prod" not in rendered
