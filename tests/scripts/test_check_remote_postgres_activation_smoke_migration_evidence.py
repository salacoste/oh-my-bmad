"""Tests for Story 134.3 remote Postgres activation smoke and migration evidence checker."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_remote_postgres_activation_smoke_migration_evidence.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "check_remote_postgres_activation_smoke_migration_evidence", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_remote_postgres_activation_smoke_migration_evidence"] = mod
    spec.loader.exec_module(mod)
    return mod


def _copy_live_fixture(tmp_path: Path, mod: object) -> None:
    for rel in [
        mod.CONTRACT_PATH,  # type: ignore[attr-defined]
        mod.FEATURE_STATUS_PATH,  # type: ignore[attr-defined]
        mod.PROJECT_OVERVIEW_PATH,  # type: ignore[attr-defined]
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
    (tmp_path / mod.CONTRACT_PATH).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")  # type: ignore[attr-defined]


def test_self_test_passes() -> None:
    mod = _load_module()
    assert mod._self_test() == 0  # type: ignore[attr-defined]


def test_live_contract_is_clean() -> None:
    mod = _load_module()
    assert mod.main([]) == 0  # type: ignore[attr-defined]


def test_missing_required_domain_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    contract = data["future_smoke_evidence_contract"]
    assert isinstance(contract, dict)
    domains = contract["required_domains"]
    assert isinstance(domains, dict)
    domains.pop("migration_preconditions")
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("required smoke evidence domains missing" in v.message for v in violations)


def test_activation_performed_true_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    boundary = data["activation_boundary"]
    assert isinstance(boundary, dict)
    boundary["activation_performed"] = True
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation_performed must be false" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Remote Postgres cutover completed successfully.",
        "The remote Postgres migration executed successfully.",
        "Live database cutover completed for remote Postgres.",
        "Remote Postgres activation proven.",
        "Remote Postgres activation proved.",
        "Database proved activation.",
    ],
)
def test_status_activation_overclaim_fails(tmp_path: Path, unsafe_text: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


def test_readiness_as_proof_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\nReadiness artifacts prove activation for remote Postgres.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("readiness-as-proof" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Readiness artifacts prove remote Postgres activation.",
        "Readiness artifacts proved database activation.",
        "Readiness artifacts have proven remote Postgres activation.",
    ],
)
def test_readiness_as_proof_with_target_between_proof_and_activation_fails(
    tmp_path: Path, unsafe_text: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("readiness-as-proof" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "No remote Postgres activation is performed, but cutover completed successfully.",
        "No remote Postgres activation is performed: cutover completed successfully.",
        "No remote Postgres activation is performed, cutover completed successfully.",
        "No remote Postgres activation is performed but cutover completed successfully.",
    ],
)
def test_direct_negation_does_not_hide_later_activation_overclaim(
    tmp_path: Path, unsafe_text: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    ("unsafe_text", "expected_message"),
    [
        (
            "No remote Postgres activation is performed, but remote Postgres activation is enabled.",
            "activation overclaim",
        ),
        (
            "No remote Postgres activation is performed: remote Postgres activation is enabled.",
            "activation overclaim",
        ),
        (
            "No remote Postgres activation is performed, remote Postgres activation is enabled.",
            "activation overclaim",
        ),
        (
            "No remote Postgres activation is performed but remote Postgres activation is enabled.",
            "activation overclaim",
        ),
        (
            "No remote Postgres activation is performed and remote Postgres activation is enabled.",
            "activation overclaim",
        ),
        (
            "No remote Postgres activation is performed: database done.",
            "activation overclaim",
        ),
        (
            "No remote Postgres activation is performed, database complete.",
            "activation overclaim",
        ),
        (
            "No remote Postgres activation is performed but database enabled.",
            "activation overclaim",
        ),
        (
            "Future/operator-gated planning only: remote Postgres activation occurred.",
            "activation overclaim",
        ),
        (
            "Future/operator-gated planning only and remote Postgres activation occurred.",
            "activation overclaim",
        ),
        (
            "Future/operator-gated planning only: remote Postgres activation live.",
            "activation overclaim",
        ),
        (
            "Future/operator-gated planning only: remote Postgres activation cut over.",
            "activation overclaim",
        ),
        (
            "Future/operator-gated planning only: readiness artifacts have proven activation for remote Postgres.",
            "readiness-as-proof",
        ),
        (
            "Readiness artifacts prove remote Postgres activation.",
            "readiness-as-proof",
        ),
        (
            "Readiness artifacts proved database activation.",
            "readiness-as-proof",
        ),
        (
            "Readiness artifacts have proven remote Postgres activation.",
            "readiness-as-proof",
        ),
        (
            "Readiness artifacts are not proof activation occurred, but readiness artifacts prove remote Postgres activation.",
            "readiness-as-proof",
        ),
        (
            "Readiness artifacts are not proof activation occurred and readiness artifacts prove remote Postgres activation.",
            "readiness-as-proof",
        ),
    ],
)
def test_cycle_4_high_false_negative_strings_fail(
    tmp_path: Path, unsafe_text: str, expected_message: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(expected_message in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Remote Postgres migration ran.",
        "Remote Postgres migration ran successfully.",
        "Remote Postgres migration has run.",
        "Remote Postgres migration was run.",
        "Migration ran for remote Postgres.",
        "Remote Postgres activation proof exists.",
        "Proof of remote Postgres activation exists.",
    ],
)
def test_cycle_6_high_false_negative_strings_fail(tmp_path: Path, unsafe_text: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Remote Postgres migration has been run.",
        "Remote Postgres migration is run.",
        "Remote Postgres migration was successfully run.",
        "Migration has been run for remote Postgres.",
        "Remote Postgres activation proof was present.",
        "Remote Postgres activation proof available.",
        "Proof of remote Postgres activation was available.",
    ],
)
def test_cycle_7_high_false_negative_strings_fail(tmp_path: Path, unsafe_text: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Future/operator-gated planning only Remote Postgres migration has been run.",
        "Future/operator-gated planning only and Remote Postgres migration is run.",
        "Future/operator-gated planning only Remote Postgres activation proof was present.",
        "Future/operator-gated planning only and Proof of remote Postgres activation was available.",
    ],
)
def test_cycle_7_safe_context_does_not_hide_positive_noun_claims(
    tmp_path: Path, unsafe_text: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Remote Postgres migrations have been run.",
        "Alembic migrations ran for remote Postgres.",
        "Remote Postgres migration has successfully been run.",
        "Migration already ran for remote Postgres.",
        "Remote Postgres migration did run.",
        "Remote Postgres migrations were applied.",
        "Database migrations ran against remote Postgres.",
    ],
)
def test_cycle_9_reviewer_migration_execution_claims_fail(tmp_path: Path, unsafe_text: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Remote Postgres database migrations have successfully been applied.",
        "Postgres migrations have already been executed.",
        "Alembic migration did execute against remote Postgres.",
        "Database migration has now been run for remote Postgres.",
        "Migrations were successfully applied against remote Postgres.",
    ],
)
def test_cycle_9_migration_execution_family_claims_fail(tmp_path: Path, unsafe_text: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Remote Postgres activation proof was submitted.",
        "Proof of remote Postgres activation was uploaded.",
        "Remote Postgres activation proof is documented.",
        "Proof of remote Postgres activation is now available.",
        "Remote Postgres activation proof is now present.",
        "Remote Postgres activation evidence exists.",
        "Evidence of remote Postgres activation was present.",
    ],
)
def test_cycle_9_reviewer_activation_proof_evidence_noun_claims_fail(
    tmp_path: Path, unsafe_text: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Remote Postgres activation evidence has now been recorded.",
        "Remote Postgres activation proof has already been accepted.",
        "Evidence of remote Postgres activation has successfully been provided.",
        "Proof of remote Postgres activation documented.",
    ],
)
def test_cycle_9_activation_proof_evidence_family_claims_fail(
    tmp_path: Path, unsafe_text: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Alembic applied migrations against remote Postgres.",
        "Alembic has applied migrations to remote Postgres.",
        "Alembic previously applied database migrations to remote Postgres.",
        "Migration tool ran migrations for remote Postgres.",
        "Database tool executed migrations against remote Postgres.",
        "The migration runner applied database migrations to remote Postgres.",
    ],
)
def test_cycle_10_alembic_and_tool_actor_migration_claims_fail(
    tmp_path: Path, unsafe_text: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Activation evidence for remote Postgres was submitted.",
        "Activation proof for remote Postgres was uploaded.",
        "Activation evidence for remote Postgres exists.",
        "Activation evidence for remote Postgres is now available.",
        "Activation proof for remote Postgres is present.",
        "Activation evidence for remote Postgres has been documented.",
        "Activation proof for remote Postgres was recorded.",
        "Activation evidence for remote Postgres has been accepted.",
        "Activation proof for remote Postgres was provided.",
    ],
)
def test_cycle_10_activation_proof_evidence_for_remote_postgres_claims_fail(
    tmp_path: Path, unsafe_text: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Activation evidence was submitted for remote Postgres.",
        "Proof was submitted for remote Postgres activation.",
        "Evidence was recorded for remote Postgres activation.",
        "Activation proof was uploaded for remote Postgres.",
        "Activation evidence has been documented for remote Postgres.",
        "Activation proof is available for remote Postgres.",
        "Proof was accepted for remote Postgres activation.",
        "Evidence has been provided for remote Postgres activation.",
        "Evidence for remote Postgres activation was present.",
        "Evidence of remote Postgres activation has been recorded.",
        "Remote Postgres evidence of activation was available.",
        "Remote Postgres proof of activation exists.",
    ],
)
def test_cycle_11_activation_proof_evidence_alternate_order_claims_fail(
    tmp_path: Path, unsafe_text: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "safe_text",
    [
        "single migration runner",
        "migration runner",
        "migration preconditions",
        "not activation proof",
        "not proof of activation",
        "not proof of remote Postgres activation",
        "Readiness artifacts are not activation proof.",
        "Activation proof was not submitted for remote Postgres.",
        "Evidence was not recorded for remote Postgres activation.",
    ],
)
def test_cycle_6_safe_regression_strings_remain_clean(tmp_path: Path, safe_text: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{safe_text}\n", encoding="utf-8")
    assert mod.validate(tmp_path) == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Readiness artifacts are not proof activation occurred, but readiness artifacts prove activation for remote Postgres.",
        "Readiness artifacts are not proof activation occurred: readiness artifacts prove activation for remote Postgres.",
        "Readiness artifacts are not proof activation occurred, readiness artifacts prove activation for remote Postgres.",
        "Readiness artifacts are not proof activation occurred but readiness artifacts prove activation for remote Postgres.",
        "Readiness artifacts are not proof activation occurred, but readiness artifacts prove remote Postgres activation.",
        "Readiness artifacts are not proof activation occurred and readiness artifacts prove remote Postgres activation.",
        "Readiness artifacts are not proof activation occurred: readiness artifacts prove remote Postgres activation.",
        "Readiness artifacts are not proof activation occurred, readiness artifacts prove database activation.",
        "Readiness artifacts are not proof activation occurred but readiness artifacts proved database activation.",
        "Readiness artifacts are not activation proof, but readiness artifacts prove remote Postgres activation.",
    ],
)
def test_direct_negation_does_not_hide_later_readiness_as_proof(
    tmp_path: Path, unsafe_text: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("readiness-as-proof" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Future planning says remote Postgres cutover completed successfully.",
        "The remote Postgres migration executed successfully after planning.",
        "Story is complete locally, and remote Postgres activation is live.",
        "Future/operator-gated planning only: remote Postgres cutover completed successfully.",
        "Future/operator-gated planning only, remote Postgres cutover completed successfully.",
        "Future/operator-gated planning only but remote Postgres cutover completed successfully.",
        "Complete locally as docs/status/checker slice: remote Postgres activation is live.",
        "Complete locally as docs/status/checker slice, remote Postgres activation is live.",
        "Complete locally as docs/status/checker slice but remote Postgres activation is live.",
        "Future/operator-gated planning only: remote Postgres cutover is done.",
        "Future/operator-gated planning only, remote Postgres cutover is done.",
        "Future/operator-gated planning only but remote Postgres cutover is done.",
        "Complete locally as docs/status/checker slice: remote Postgres activation enabled.",
        "Complete locally as docs/status/checker slice, remote Postgres activation enabled.",
        "Complete locally as docs/status/checker slice but remote Postgres activation enabled.",
        "This is not proof activation occurred: remote Postgres activation active.",
        "Future/operator-gated planning only: remote Postgres activation complete.",
        "Future/operator-gated planning only: remote Postgres activation activated.",
    ],
)
def test_broad_safe_words_do_not_hide_activation_overclaim(
    tmp_path: Path, unsafe_text: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "This is not proof, but readiness artifacts prove activation for remote Postgres.",
        "This is not proof activation occurred: readiness artifacts prove activation for remote Postgres.",
        "This is not proof activation occurred, readiness artifacts prove activation for remote Postgres.",
        "This is not proof activation occurred but readiness artifacts prove activation for remote Postgres.",
    ],
)
def test_broad_not_proof_does_not_hide_readiness_as_proof(tmp_path: Path, unsafe_text: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("readiness-as-proof" in v.message for v in violations)


def test_missing_just_wiring_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    justfile = tmp_path / mod.JUSTFILE_PATH  # type: ignore[attr-defined]
    justfile.write_text(
        justfile.read_text(encoding="utf-8").replace(mod.CHECKER_COMMAND, "", 1),  # type: ignore[attr-defined]
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("lint must run Story 134.3 checker" in v.message for v in violations)


def test_story_status_backlog_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    status = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    status.write_text(
        status.read_text(encoding="utf-8").replace(
            "134-3-remote-postgres-activation-smoke-migration-evidence-package: done",
            "134-3-remote-postgres-activation-smoke-migration-evidence-package: backlog",
            1,
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("Story 134.3 must be done" in v.message for v in violations)


def test_story_status_closed_fails_for_134_3(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    status = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    status.write_text(
        status.read_text(encoding="utf-8").replace(
            "134-3-remote-postgres-activation-smoke-migration-evidence-package: done",
            "134-3-remote-postgres-activation-smoke-migration-evidence-package: closed",
            1,
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("Story 134.3 must be done" in v.message for v in violations)


def test_sprint_status_secret_like_material_scans_unrelated_lines(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    status = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    dsn = "postgres" + "ql://user:pass@db.example.invalid:5432/app"
    status.write_text(
        status.read_text(encoding="utf-8") + f"\nunrelated_metadata: {dsn}\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)


def test_sprint_status_story_134_3_audit_continuation_claim_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    assert mod.validate(tmp_path) == []  # type: ignore[attr-defined]

    status = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    status.write_text(
        status.read_text(encoding="utf-8")
        + "\nunrelated_metadata: future remote Postgres planning remains queued\n",
        encoding="utf-8",
    )
    assert mod.validate(tmp_path) == []  # type: ignore[attr-defined]

    text = status.read_text(encoding="utf-8")
    status.write_text(
        text.replace(
            "      remote Postgres activation smoke and migration evidence only. It performs no remote",
            "      Postgres activation proof was submitted for remote Postgres activation.",
            1,
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(
        v.location.startswith(str(mod.SPRINT_STATUS_PATH))  # type: ignore[attr-defined]
        and "activation overclaim" in v.message
        for v in violations
    )


def test_sprint_status_unrelated_pr_134_note_is_not_directly_relevant(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    status = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    status.write_text(
        status.read_text(encoding="utf-8")
        + "\npr_note: PR #134 remote Postgres activation proof was submitted\n",
        encoding="utf-8",
    )

    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]

    assert not any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "relevant_prefix",
    [
        "story_note: Story 134",
        "story_note: story-134",
        "story_note: 134-3-remote-postgres-activation-smoke-migration-evidence-package",
        "story_note: 134.3",
        "story_note: Epic 134",
        "story_note: epic-134",
    ],
)
def test_sprint_status_story_epic_134_relevant_lines_still_scan_activation_overclaim(
    tmp_path: Path, relevant_prefix: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    status = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    status.write_text(
        status.read_text(encoding="utf-8")
        + f"\n{relevant_prefix} remote Postgres activation proof was submitted\n",
        encoding="utf-8",
    )

    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]

    assert any(
        v.location.startswith(str(mod.SPRINT_STATUS_PATH))  # type: ignore[attr-defined]
        and "activation overclaim" in v.message
        for v in violations
    )


def test_secret_like_contract_material_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["unsafe_secret"] = "postgres" + "ql://user:pass@db.example.invalid:5432/app"
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)
