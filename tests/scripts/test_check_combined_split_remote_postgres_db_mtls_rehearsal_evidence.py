"""Tests for Story 134.5 combined rehearsal evidence checker."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = (
    REPO_ROOT / "scripts" / "check_combined_split_remote_postgres_db_mtls_rehearsal_evidence.py"
)


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "check_combined_split_remote_postgres_db_mtls_rehearsal_evidence", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_combined_split_remote_postgres_db_mtls_rehearsal_evidence"] = mod
    spec.loader.exec_module(mod)
    return mod


def _copy_live_fixture(tmp_path: Path, mod: object) -> None:
    fixture_paths = {
        mod.CONTRACT_PATH,  # type: ignore[attr-defined]
        mod.FEATURE_STATUS_PATH,  # type: ignore[attr-defined]
        mod.PROJECT_OVERVIEW_PATH,  # type: ignore[attr-defined]
        mod.SPRINT_STATUS_PATH,  # type: ignore[attr-defined]
        mod.ARTIFACT_PATH,  # type: ignore[attr-defined]
        mod.CLOSURE_ARTIFACT_PATH,  # type: ignore[attr-defined]
        mod.JUSTFILE_PATH,  # type: ignore[attr-defined]
        mod.CI_PATH,  # type: ignore[attr-defined]
        *(Path(ref) for ref in mod.REQUIRED_READINESS_REFS),  # type: ignore[attr-defined]
    }
    for rel in fixture_paths:
        src = REPO_ROOT / rel
        if rel == mod.CLOSURE_ARTIFACT_PATH and not src.exists():  # type: ignore[attr-defined]
            continue
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _write_story_134_6_closure_fixture(tmp_path: Path, mod: object) -> None:
    closure_path = tmp_path / mod.CLOSURE_ARTIFACT_PATH  # type: ignore[attr-defined]
    closure_path.parent.mkdir(parents=True, exist_ok=True)
    closure_path.write_text(
        "# Story 134.6 Controlled Activation Closure and Go/No-Go Evidence\n\n"
        "Story 134.6 closes Phase 51 / Epic 134 as planning-only/docs-status "
        "evidence, not activation. Split deployment, remote Postgres, and DB "
        "mTLS smoke evidence remain future/operator-gated.\n",
        encoding="utf-8",
    )


def _normalize_expected_done_fixture(tmp_path: Path, mod: object) -> None:
    mod._normalize_expected_done_fixture(tmp_path)  # type: ignore[attr-defined]


def _load_contract(tmp_path: Path, mod: object) -> dict[str, object]:
    raw: object = json.loads((tmp_path / mod.CONTRACT_PATH).read_text(encoding="utf-8"))  # type: ignore[attr-defined]
    assert isinstance(raw, dict)
    return cast("dict[str, object]", raw)


def _write_contract(tmp_path: Path, mod: object, data: dict[str, object]) -> None:
    (tmp_path / mod.CONTRACT_PATH).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")  # type: ignore[attr-defined]


def test_self_test_passes() -> None:
    mod = _load_module()
    assert mod._self_test() == 0  # type: ignore[attr-defined]


def test_live_contract_is_clean() -> None:
    mod = _load_module()
    assert mod.main([]) == 0  # type: ignore[attr-defined]


def test_story_134_6_planning_closure_status_is_allowed(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    _write_story_134_6_closure_fixture(tmp_path, mod)
    status = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    status.write_text(
        status.read_text(encoding="utf-8")
        .replace(
            "epic-134: in-progress",
            "epic-134: done  # Story 134.6 planning-only/docs-status closure, not activation",
        )
        .replace(
            "134-6-controlled-activation-closure-go-no-go-evidence: backlog",
            "134-6-controlled-activation-closure-go-no-go-evidence: done  # Story 134.6 docs/status-only closure, not activation",
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert not violations


def test_missing_required_domain_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    contract = data["future_rehearsal_evidence_contract"]
    assert isinstance(contract, dict)
    domains = contract["required_domains"]
    assert isinstance(domains, dict)
    domains.pop("combined_rehearsal_smoke_trace")
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("required rehearsal evidence domains missing" in v.message for v in violations)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("activation_performed", "activation_performed must be false"),
        ("rehearsal_performed", "rehearsal_performed must be false"),
    ],
)
def test_activation_or_rehearsal_performed_true_fails(
    tmp_path: Path, field: str, message: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    boundary = data["activation_boundary"]
    assert isinstance(boundary, dict)
    boundary[field] = True
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(message in v.message for v in violations)


def test_production_script_change_boundary_false_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    boundary = data["activation_boundary"]
    assert isinstance(boundary, dict)
    boundary["no_production_script_change"] = False
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("no_production_script_change must be true" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Remote Postgres activation completed successfully.",
        "Remote Postgres was activated successfully.",
        "Remote Postgres ran successfully.",
        "Remote-Postgres activation completed successfully.",
        "Split deployment activation is live.",
        "split deployment is live.",
        "split deployment has been run successfully.",
        "DB mTLS production activation occurred.",
        "DB mTLS is enabled in production.",
        "DB mTLS successfully ran.",
        "Live database cutover executed.",
        "Migration execution completed.",
        "Migration was executed successfully.",
        "Migration ran successfully.",
        "Migration successfully completed.",
        "Migration successfully executed.",
        "Migration was run successfully.",
        "Migration has successfully been executed.",
        "Migration has successfully been completed.",
        "Migrations have successfully been executed.",
        "Database migrations have successfully been completed.",
    ],
)
def test_activation_overclaim_fails(tmp_path: Path, unsafe_text: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Combined rehearsal completed successfully.",
        "The live combined rehearsal occurred.",
        "Combined split deployment remote Postgres DB mTLS rehearsal succeeded.",
        "Live rehearsal completed successfully.",
        "The live rehearsal occurred.",
        "The rehearsal was performed successfully.",
    ],
)
def test_rehearsal_overclaim_fails(tmp_path: Path, unsafe_text: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("rehearsal overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Plaintext fallback is enabled for emergencies.",
        "Plaintext fallback available after failure.",
        "Plaintext-fallback was accepted.",
        "Operators allowed plaintext fallback.",
    ],
)
def test_plaintext_fallback_overclaim_fails(tmp_path: Path, unsafe_text: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("plaintext fallback overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Readiness artifacts prove DB mTLS activation and rehearsal.",
        "Readiness evidence has proven the combined rehearsal.",
        "Readiness prerequisites are proof activation occurred.",
    ],
)
def test_readiness_as_proof_fails(tmp_path: Path, unsafe_text: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("readiness-as-proof" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN CERTIFICATE-----",
        "postgresql://user:password@example.com/db",
        "dsn=postgresql://user:password@example.com/db",
        "client_cert=/tmp/client.crt",
        "private_key=supersecret",
    ],
)
def test_secret_like_material_fails(tmp_path: Path, unsafe_text: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.ARTIFACT_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "rollback execution completed successfully.",
        "rollback was executed successfully.",
        "Rollback ran successfully.",
        "Rollback has been run successfully.",
        "Rollback successfully completed.",
        "Rollback successfully ran.",
        "Rollback has successfully been run.",
        "Rollbacks have successfully been run.",
        "Rollback was changed successfully.",
        "restore execution completed successfully.",
        "Restore ran successfully.",
        "Restore has been run successfully.",
        "Restore successfully completed.",
        "Restore has successfully been completed.",
        "Restores have successfully been completed.",
        "Restore successfully updated.",
        "destructive operation completed successfully.",
        "production host mutation completed successfully.",
        "credential use completed successfully.",
        "credential was used.",
        "credentials were used.",
        "production credentials were used successfully.",
        "Production credentials were applied successfully.",
        "Production credentials have been applied successfully.",
        "Production credentials successfully applied.",
        "Production credentials have successfully been applied.",
        "Production credentials have successfully been used.",
        "production-state change completed successfully.",
        "production state was changed.",
        "Runtime behavior change completed successfully.",
        "runtime behavior change ran successfully.",
        "Deployment config change completed successfully.",
        "deployment config change has been run successfully.",
        "operator/deployment/rollback/restore/migration/activation/production script change completed successfully.",
        "operator/deployment/rollback/restore/migration/activation/production scripts updated successfully.",
        "deployment script was changed.",
    ],
)
def test_planning_story_forbidden_change_overclaim_fails(tmp_path: Path, unsafe_text: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("planning-story forbidden change" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Story 134.5 remains future/operator-gated planning only; rollback execution completed successfully.",
        "Story 134.5 remains future/operator-gated planning only; restore execution completed successfully.",
        "Story 134.5 remains future/operator-gated planning only; destructive operation completed successfully.",
        "Story 134.5 remains future/operator-gated planning only; production host mutation completed successfully.",
        "Story 134.5 remains future/operator-gated planning only; credential use completed successfully.",
        "Story 134.5 remains future/operator-gated planning only; credential was used.",
        "Story 134.5 remains future/operator-gated planning only; production-state change completed successfully.",
        "Story 134.5 remains future/operator-gated planning only; production state was changed.",
        "Story 134.5 remains future/operator-gated planning only; operator/deployment/rollback/restore/migration/activation/production script change completed successfully.",
        "Story 134.5 remains future/operator-gated planning only; operator/deployment/rollback/restore/migration/activation/production scripts updated successfully.",
        "Story 134.5 remains future/operator-gated planning only; deployment script was changed.",
    ],
)
def test_safe_prefix_does_not_mask_forbidden_change_overclaim(
    tmp_path: Path, unsafe_text: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("planning-story forbidden change" in v.message for v in violations)


@pytest.mark.parametrize(
    ("unsafe_text", "expected_message"),
    [
        (
            "Story 134.5 remains future/operator-gated planning only; Remote Postgres activation completed successfully.",
            "activation overclaim",
        ),
        (
            "Story 134.5 remains future/operator-gated planning only; Combined rehearsal completed successfully.",
            "rehearsal overclaim",
        ),
        (
            "Story 134.5 remains future/operator-gated planning only; Plaintext fallback is enabled for emergencies.",
            "plaintext fallback overclaim",
        ),
        (
            "Story 134.5 remains future/operator-gated planning only; Readiness artifacts prove DB mTLS activation and rehearsal.",
            "readiness-as-proof",
        ),
        (
            "Story 134.5 remains future/operator-gated planning only; Self-attestation is approved as sufficient evidence.",
            "self-attestation",
        ),
    ],
)
def test_safe_prefix_does_not_mask_core_overclaims(
    tmp_path: Path, unsafe_text: str, expected_message: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(expected_message in v.message for v in violations)


@pytest.mark.parametrize(
    ("unsafe_text", "expected_message"),
    [
        (
            "Story 134.5 remains future/operator-gated planning only, Remote Postgres activation completed successfully.",
            "activation overclaim",
        ),
        (
            "Story 134.5 is complete locally as an evidence package and Remote Postgres activation completed successfully.",
            "activation overclaim",
        ),
        (
            "Story 134.5 remains future/operator-gated planning only and Remote Postgres activation completed successfully.",
            "activation overclaim",
        ),
        (
            "Story 134.5 remains future/operator-gated planning only — Remote Postgres activation completed successfully.",
            "activation overclaim",
        ),
        (
            "Story 134.5 remains future/operator-gated planning only: Remote Postgres activation completed successfully.",
            "activation overclaim",
        ),
        (
            "Story 134.5 remains future/operator-gated planning only, Combined rehearsal completed successfully.",
            "rehearsal overclaim",
        ),
        (
            "Story 134.5 remains future/operator-gated planning only - Combined rehearsal completed successfully.",
            "rehearsal overclaim",
        ),
        (
            "Story 134.5 remains future/operator-gated planning only, Plaintext fallback is enabled for emergencies.",
            "plaintext fallback overclaim",
        ),
        (
            "Story 134.5 remains future/operator-gated planning only, Readiness artifacts prove DB mTLS activation and rehearsal.",
            "readiness-as-proof",
        ),
        (
            "Story 134.5 remains future/operator-gated planning only, Self-attestation is approved as sufficient evidence.",
            "self-attestation",
        ),
    ],
)
def test_same_clause_safe_prefix_does_not_mask_core_overclaims(
    tmp_path: Path, unsafe_text: str, expected_message: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(expected_message in v.message for v in violations)


@pytest.mark.parametrize(
    ("unsafe_text", "expected_message"),
    [
        (
            "No operator approval exists, Remote Postgres activation completed successfully.",
            "activation overclaim",
        ),
        (
            "No operator approval exists and Remote Postgres activation completed successfully.",
            "activation overclaim",
        ),
        (
            "No operator approval exists — Combined rehearsal completed successfully.",
            "rehearsal overclaim",
        ),
        (
            "No operator approval exists: Plaintext fallback is enabled for emergencies.",
            "plaintext fallback overclaim",
        ),
        (
            "No operator approval exists, Readiness artifacts prove DB mTLS activation and rehearsal.",
            "readiness-as-proof",
        ),
    ],
)
def test_unrelated_no_prefix_does_not_mask_overclaims(
    tmp_path: Path, unsafe_text: str, expected_message: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(expected_message in v.message for v in violations)


def test_self_attestation_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\nSelf-attestation is approved as sufficient evidence.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("self-attestation" in v.message for v in violations)


def test_safe_future_operator_gated_language_is_clean(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    _normalize_expected_done_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\nStory 134.5 combined rehearsal evidence remains future/operator-gated planning only; no activation, no rehearsal, no live database cutover, no operator/deployment/rollback/restore/migration/activation/production script change, and no plaintext fallback are performed. Static checker/test/CI gate wiring is local validation only.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert not violations


def test_missing_just_wiring_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    just_path = tmp_path / mod.JUSTFILE_PATH  # type: ignore[attr-defined]
    just_lines = just_path.read_text(encoding="utf-8").splitlines()
    just_path.write_text(
        "\n".join(line for line in just_lines if line.strip() != mod.CHECKER_COMMAND) + "\n",  # type: ignore[attr-defined]
        encoding="utf-8",
    )
    assert mod.CHECKER_SELF_TEST_COMMAND in just_path.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("checker command missing from justfile" in v.message for v in violations)


def test_missing_ci_wiring_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    ci_path = tmp_path / mod.CI_PATH  # type: ignore[attr-defined]
    ci_lines = ci_path.read_text(encoding="utf-8").splitlines()
    ci_path.write_text(
        "\n".join(
            line
            for line in ci_lines
            if line.strip() not in {mod.CHECKER_COMMAND, f"run: {mod.CHECKER_COMMAND}"}  # type: ignore[attr-defined]
        )
        + "\n",
        encoding="utf-8",
    )
    assert mod.CHECKER_SELF_TEST_COMMAND in ci_path.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("checker command missing from CI" in v.message for v in violations)


def test_status_134_5_not_done_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    sprint_path = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    sprint_path.write_text(
        sprint_path.read_text(encoding="utf-8").replace(
            "134-5-combined-split-remote-postgres-db-mtls-rehearsal: done",
            "134-5-combined-split-remote-postgres-db-mtls-rehearsal: backlog",
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("Story 134.5 must be marked done" in v.message for v in violations)


def test_story_134_6_not_backlog_fails_without_closure(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    closure_path = tmp_path / mod.CLOSURE_ARTIFACT_PATH  # type: ignore[attr-defined]
    if closure_path.exists():
        closure_path.unlink()
    sprint_path = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    text = sprint_path.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^(\s*epic-134:\s*)\S+.*$", r"\1in-progress", text)
    text = re.sub(
        r"(?m)^(\s*134-6-controlled-activation-closure-go-no-go-evidence:\s*)\S+.*$",
        r"\1done",
        text,
    )
    sprint_path.write_text(text, encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("Story 134.6 must remain backlog" in v.message for v in violations)
