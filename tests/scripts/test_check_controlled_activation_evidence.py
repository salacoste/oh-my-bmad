"""Tests for Story 134.1 controlled activation evidence checker."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_controlled_activation_evidence.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "check_controlled_activation_evidence", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_controlled_activation_evidence"] = mod
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
    (tmp_path / mod.CONTRACT_PATH).write_text(json.dumps(data, indent=2), encoding="utf-8")  # type: ignore[attr-defined]


def _valid_future_evidence(mod: object) -> dict[str, object]:
    evidence = {
        field: f"ref-{field}"
        for field in mod.REQUIRED_EVIDENCE_FIELDS  # type: ignore[attr-defined]
    }
    now = datetime.now(UTC).replace(microsecond=0)
    evidence["change_window_utc"] = {
        "starts_at_utc": (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
        "ends_at_utc": (now + timedelta(minutes=70)).isoformat().replace("+00:00", "Z"),
    }
    evidence["readiness_prerequisites"] = ["split-ref", "postgres-ref", "mtls-ref"]
    evidence["activation_intent"] = "future_operator_gated bounded activation intent only"
    evidence["evidence_retention"] = "retain sanitized evidence per ops retention policy ref"
    evidence["redaction_statement"] = (
        "sanitized evidence package contains no plaintext secrets or certificate material"
    )
    evidence["trace_correlation"] = {
        "operation_id": "op-1",
        "trace_id": "trace-1",
        "audit_event_refs": ["audit-1"],
    }
    evidence["generated_at_utc"] = now.isoformat().replace("+00:00", "Z")
    evidence["expires_at_utc"] = (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    return evidence


def test_self_test_passes() -> None:
    mod = _load_module()
    assert mod._self_test() == 0  # type: ignore[attr-defined]


def test_copy_fixture_includes_optional_story_134_6_closure_artifact(tmp_path: Path) -> None:
    mod = _load_module()
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    _copy_live_fixture(src, mod)
    _write_story_134_6_closure_fixture(src, mod)

    mod._copy_fixture(src, dest)  # type: ignore[attr-defined]

    closure = dest / mod.CLOSURE_ARTIFACT_PATH  # type: ignore[attr-defined]
    assert closure.exists()
    assert "planning-only/docs-status" in closure.read_text(encoding="utf-8")


def test_live_contract_is_clean() -> None:
    mod = _load_module()
    assert mod.main([]) == 0  # type: ignore[attr-defined]


def test_missing_required_evidence_field_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    contract = data["evidence_package_contract"]
    assert isinstance(contract, dict)
    fields = contract["required_fields"]
    assert isinstance(fields, dict)
    fields.pop("operator_approval_ref")
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("required evidence fields missing" in v.message for v in violations)


def test_contract_required_fields_include_story_ac_domain_fields(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    contract = data["evidence_package_contract"]
    assert isinstance(contract, dict)
    fields = contract["required_fields"]
    assert isinstance(fields, dict)
    for field in ("activation_intent", "evidence_retention", "redaction_statement"):
        assert field in fields
        field_contract = fields[field]
        assert isinstance(field_contract, dict)
        assert field_contract.get("required") is True
        fields.pop(field)
        _write_contract(tmp_path, mod, data)
        violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
        assert any("required evidence fields missing" in v.message for v in violations)
        _copy_live_fixture(tmp_path, mod)
        data = _load_contract(tmp_path, mod)
        contract = data["evidence_package_contract"]
        assert isinstance(contract, dict)
        fields = contract["required_fields"]
        assert isinstance(fields, dict)


def test_activation_overclaim_in_status_docs_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8") + "\nProduction activation completed successfully.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


def test_readiness_as_proof_language_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.ARTIFACT_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\nReadiness artifacts are accepted as proof of production activation.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("readiness-as-proof" in v.message for v in violations)


def test_secret_like_contract_material_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["bad_material"] = "token=abcdefghijklmnopqrstuvwxyz123456"
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)


def test_plaintext_fallback_allowance_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8") + "\nPlaintext fallback is allowed during rollback.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("plaintext fallback" in v.message for v in violations)


def test_staleness_policy_not_fail_closed_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    staleness = data["staleness_policy"]
    assert isinstance(staleness, dict)
    staleness["outcome"] = "warn_only"
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("fail-closed" in v.message for v in violations)


def test_missing_story_134_1_done_status_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    text = target.read_text(encoding="utf-8").replace(
        "134-1-activation-evidence-schema-preflight-gate: done",
        "134-1-activation-evidence-schema-preflight-gate: backlog",
    )
    target.write_text(text, encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("Story 134.1" in v.message and "done/closed" in v.message for v in violations)


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


def test_story_134_6_planning_closure_status_is_allowed(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    _write_story_134_6_closure_fixture(tmp_path, mod)

    sprint = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    sprint_text = sprint.read_text(encoding="utf-8")
    sprint_text = sprint_text.replace(
        "epic-134: in-progress",
        "epic-134: done  # Phase 51 / Epic 134 controlled activation evidence planning is closed as planning-only/docs-status evidence; no live activation occurred",
    )
    sprint_text = sprint_text.replace(
        "134-6-controlled-activation-closure-go-no-go-evidence: backlog",
        "134-6-controlled-activation-closure-go-no-go-evidence: done  # Story 134.6 docs/status-only closure, not activation",
    )
    sprint_text += (
        '\n  - date: "2026-07-11"\n'
        "    event: story-134-6-controlled-activation-closure-go-no-go-evidence-done\n"
        "    summary: >-\n"
        "      Story 134.6 is complete as docs/status-only closure evidence. Epic 134 is\n"
        "      marked done for planning-only evidence closure; no split deployment activation,\n"
        "      no remote Postgres activation, no DB mTLS production activation, no live rehearsal,\n"
        "      and no production-state change occurred.\n"
    )
    sprint.write_text(sprint_text, encoding="utf-8")

    feature = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    feature.write_text(
        feature.read_text(encoding="utf-8")
        + (
            "\nEpic 134 controlled production activation evidence planning is done as "
            "planning-only/docs-status closure, not activation. Story 134.6 records "
            "docs/status-only closure, and split deployment, remote Postgres, and DB "
            "mTLS smoke evidence remain future/operator-gated. This is not proof "
            "activation occurred. No live activation.\n"
        ),
        encoding="utf-8",
    )

    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert not violations


def test_story_134_6_done_without_closure_artifact_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    sprint = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    sprint.write_text(
        sprint.read_text(encoding="utf-8").replace(
            "epic-134: in-progress",
            "epic-134: done",
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("Story 134.6 planning-only closure evidence exists" in v.message for v in violations)


def test_story_134_6_closure_still_rejects_activation_overclaim(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    _write_story_134_6_closure_fixture(tmp_path, mod)
    feature = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    feature.write_text(
        feature.read_text(encoding="utf-8")
        + "\nNo live activation; production activation completed successfully.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "suffix",
    [
        ", production activation completed successfully",
        " and activation completed successfully",
        " activation completed successfully",
    ],
)
def test_story_134_6_status_line_suffix_overclaim_still_fails(tmp_path: Path, suffix: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    _write_story_134_6_closure_fixture(tmp_path, mod)
    sprint = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    sprint.write_text(
        sprint.read_text(encoding="utf-8").replace(
            "134-6-controlled-activation-closure-go-no-go-evidence: backlog",
            "134-6-controlled-activation-closure-go-no-go-evidence: done  # "
            f"Story 134.6 docs/status-only closure, not activation{suffix}",
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


def test_story_134_6_slug_does_not_sanitize_activation_overclaim(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8")
        + (
            "\nevent: story-134-6-controlled-activation-closure-go-no-go-evidence-done; "
            "production activation completed successfully\n"
            "epic: epic-134\n"
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


def test_story_134_6_closure_artifact_is_scanned_for_overclaims(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    _write_story_134_6_closure_fixture(tmp_path, mod)
    closure = tmp_path / mod.CLOSURE_ARTIFACT_PATH  # type: ignore[attr-defined]
    closure.write_text(
        closure.read_text(encoding="utf-8") + "\nProduction activation completed successfully.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


def test_story_134_6_closure_artifact_is_scanned_for_secret_values(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    _write_story_134_6_closure_fixture(tmp_path, mod)
    closure = tmp_path / mod.CLOSURE_ARTIFACT_PATH  # type: ignore[attr-defined]
    closure.write_text(
        closure.read_text(encoding="utf-8") + "\nSECRET_KEY=abcdefghijklmnopqrstuvwxyz123456\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)


def test_story_134_6_done_requires_story_status_done_when_artifact_exists(
    tmp_path: Path,
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    _write_story_134_6_closure_fixture(tmp_path, mod)
    sprint = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    sprint.write_text(
        sprint.read_text(encoding="utf-8").replace(
            "epic-134: in-progress",
            "epic-134: done",
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("Story 134.6 closure status must be done/closed" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "no remote Postgres activation, remote Postgres activation completed successfully",
        "no remote Postgres activation, activation completed successfully",
        "no DB mTLS production activation, production activation completed successfully",
        "no DB mTLS production activation, activation completed successfully",
    ],
)
def test_story_134_6_scoped_negation_does_not_sanitize_same_clause_overclaim(
    tmp_path: Path, unsafe_text: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8")
        + (
            '\n  - date: "2026-07-11"\n'
            "    event: story-134-6-regression\n"
            "    epic: epic-134\n"
            "    summary: >-\n"
            f"      {unsafe_text}\n"
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


def test_self_attestation_acceptance_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.ARTIFACT_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\nLeader self-attestation is accepted as review evidence.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("self-attestation" in v.message for v in violations)


def test_activation_overclaim_with_unrelated_negation_still_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\nProduction activation completed successfully; no credentials included.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


def test_unredacted_postgres_url_without_password_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.ARTIFACT_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\nEndpoint: postgresql://prod-db.example.com/registry\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)


def test_activation_overclaim_with_reordered_negation_still_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\nNo credentials included; production activation completed successfully.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


def test_unredacted_postgres_url_scanned_even_without_status_keyword(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\nEndpoint: postgresql://prod-db.example.com/registry\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)


def test_safe_negation_clause_does_not_sanitize_later_activation_overclaim(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\nNo live activation; production activation completed successfully.\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


def test_future_evidence_safe_negation_clause_does_not_sanitize_overclaim(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["smoke_scope"] = "No live activation; production activation completed successfully."
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "No live activation, production activation completed successfully.",
        "No live activation and production activation completed successfully.",
        "Production activation completed successfully, no live activation.",
        "No live activation. Production activation completed successfully.",
    ],
)
def test_mixed_safe_negation_does_not_sanitize_overclaim_forms(
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
        "No live activation, production activation completed successfully.",
        "No live activation and production activation completed successfully.",
        "Production activation completed successfully, no live activation.",
        "No live activation. Production activation completed successfully.",
    ],
)
def test_future_evidence_mixed_safe_negation_does_not_sanitize_overclaim_forms(
    tmp_path: Path, unsafe_text: str
) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["smoke_scope"] = unsafe_text
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


def test_future_evidence_package_rejects_activation_overclaim_and_string_true(
    tmp_path: Path,
) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["activation_performed"] = "true"
    evidence["smoke_scope"] = "Production activation completed successfully."
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("activation_performed" in v.message for v in violations)
    assert any("activation overclaim" in v.message for v in violations)


def test_structured_json_secret_key_value_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["nested"] = {"token": "abcdefghijklmnopqrstuvwxyz123456"}
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("structured JSON secret" in v.message for v in violations)


def test_compound_structured_json_secret_key_value_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["nested"] = {"access_token": "abcdefghijklmnopqrstuvwxyz123456"}
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("structured JSON secret" in v.message for v in violations)


@pytest.mark.parametrize(
    "key",
    [
        "private_key",
        "credential",
        "certificate",
        "POSTGRES_PASSWORD",
        "SECRET_KEY",
        "API_KEY",
        "PRIVATE_KEY",
    ],
)
def test_additional_structured_json_secret_key_values_fail(tmp_path: Path, key: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["nested"] = {key: "abcdefghijklmnopqrstuvwxyz123456"}
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("structured JSON secret" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "POSTGRES_PASSWORD=abcdefghijklmnopqrstuvwxyz123456",
        "SECRET_KEY=abcdefghijklmnopqrstuvwxyz123456",
        "private_key=abcdefghijklmnopqrstuvwxyz123456",
        "credential=abcdefghijklmnopqrstuvwxyz123456",
        "certificate=abcdefghijklmnopqrstuvwxyz123456",
        "SECRET_KEY=shortkey",
        "API_KEY=abc123",
        "SECRET_KEY shortkey",
        "`SECRET_KEY` shortkey",
        '"SECRET_KEY": "shortkey"',
        "SECRET_KEY abc",
        "`SECRET_KEY` abc",
        '"SECRET_KEY": "abcdefghijklmnopqrstuvwxyz123456"',
        '"POSTGRES_PASSWORD": "abcdefghijklmnopqrstuvwxyz123456"',
    ],
)
def test_status_docs_env_secret_variants_fail(tmp_path: Path, unsafe_text: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "POSTGRES_PASSWORD=abcdefghijklmnopqrstuvwxyz123456",
        "SECRET_KEY=abcdefghijklmnopqrstuvwxyz123456",
        "private_key=abcdefghijklmnopqrstuvwxyz123456",
        "SECRET_KEY shortkey",
        "`SECRET_KEY` shortkey",
        "SECRET_KEY=shortkey",
        "SECRET_KEY abc",
        "`SECRET_KEY` abc",
        "API_KEY=abc123",
        '"SECRET_KEY": "shortkey"',
        "credential=abcdefghijklmnopqrstuvwxyz123456",
        "certificate=abcdefghijklmnopqrstuvwxyz123456",
        '"SECRET_KEY": "abcdefghijklmnopqrstuvwxyz123456"',
        '"POSTGRES_PASSWORD": "abcdefghijklmnopqrstuvwxyz123456"',
    ],
)
def test_future_evidence_env_secret_variants_fail(tmp_path: Path, unsafe_text: str) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["smoke_scope"] = unsafe_text
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("secret-like or unredacted DSN" in v.message for v in violations)


@pytest.mark.parametrize(
    "key",
    ["POSTGRES_PASSWORD", "SECRET_KEY", "API_KEY", "PRIVATE_KEY"],
)
def test_future_evidence_structured_env_secret_keys_fail(tmp_path: Path, key: str) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["secrets"] = {key: "prodpass"}
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("structured secret" in v.message for v in violations)


def test_story_134_sprint_status_line_overclaim_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    text = target.read_text(encoding="utf-8").replace(
        "134-1-activation-evidence-schema-preflight-gate: done",
        "134-1-activation-evidence-schema-preflight-gate: done # production activation completed successfully",
    )
    target.write_text(text, encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


def test_story_134_3_sprint_status_done_line_is_allowed(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    text = target.read_text(encoding="utf-8")
    line_no = next(
        idx
        for idx, line in enumerate(text.splitlines(), start=1)
        if "134-3-remote-postgres-activation-smoke-migration-evidence-package: done" in line
    )
    violations = mod._scan_text_for_forbidden(mod.SPRINT_STATUS_PATH, text)  # type: ignore[attr-defined]
    assert not any(
        v.location == f"{mod.SPRINT_STATUS_PATH}:{line_no}"  # type: ignore[attr-defined]
        and "activation overclaim" in v.message
        for v in violations
    )


def test_story_134_3_sprint_status_done_line_with_unsafe_overclaim_fails(
    tmp_path: Path,
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    text = target.read_text(encoding="utf-8").replace(
        "Docs/status/static-checker remote Postgres smoke/migration evidence package planning only;",
        "Docs/status/static-checker remote Postgres smoke/migration evidence package planning only; "
        "production activation completed successfully;",
    )
    target.write_text(text, encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


def test_story_134_3_sprint_status_done_line_with_comma_remote_postgres_overclaim_fails(
    tmp_path: Path,
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    text = target.read_text(encoding="utf-8").replace(
        "Docs/status/static-checker remote Postgres smoke/migration evidence package planning only;",
        "Docs/status/static-checker remote Postgres smoke/migration evidence package planning only, "
        "remote Postgres activation completed successfully;",
    )
    target.write_text(text, encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


def test_story_134_sprint_status_audit_event_overclaim_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    text = target.read_text(encoding="utf-8").replace(
        "event: story-134-1-controlled-evidence-schema-preflight-gate-local-finished",
        "event: story-134-1-production-activation-completed-successfully",
    )
    target.write_text(text, encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


def test_sprint_status_current_phase_overclaim_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    text = target.read_text(encoding="utf-8").replace(
        "production activation, live rehearsal, Postgres provisioning, production host mutation, credentials/certs, migration execution, operator/deployment/rollback/restore/migration/activation/production script change, production-state change, real certificate material, and plaintext fallback remain fail-closed/deferred.",
        "Production activation completed successfully.",
    )
    target.write_text(text, encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


def test_future_evidence_package_structured_secret_fails(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["token"] = "abcdefghijklmnopqrstuvwxyz123456"
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("structured secret" in v.message for v in violations)


def test_future_evidence_package_accepts_valid_shape(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["activation_performed"] = False
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    assert mod.validate_evidence_package(evidence_path) == []  # type: ignore[attr-defined]

    evidence["target_environment"] = "postgresql://prod-db.example.com/registry"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("unredacted DSN" in v.message for v in violations)


def test_future_evidence_package_stale_timestamp_fails_closed(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["generated_at_utc"] = "2020-01-01T00:00:00Z"
    evidence["expires_at_utc"] = "2020-01-02T00:00:00Z"
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("stale/expired" in v.message for v in violations)


def test_future_evidence_package_stale_generated_at_fails_closed(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["generated_at_utc"] = "2020-01-01T00:00:00Z"
    evidence["expires_at_utc"] = "2099-01-02T00:00:00Z"
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("generated_at_utc is stale" in v.message for v in violations)


def test_future_evidence_package_future_generated_at_fails_closed(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["generated_at_utc"] = "2099-01-01T00:00:00Z"
    evidence["expires_at_utc"] = "2099-01-02T00:00:00Z"
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("generated_at_utc must not be in the future" in v.message for v in violations)


def test_future_evidence_package_malformed_change_window_fails_closed(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["change_window_utc"] = "not-a-utc-window"
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("change_window_utc must be an object" in v.message for v in violations)


def test_future_evidence_package_change_window_order_fails_closed(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["change_window_utc"] = {
        "starts_at_utc": "2099-01-01T02:00:00Z",
        "ends_at_utc": "2099-01-01T01:00:00Z",
    }
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("change_window_utc.ends_at_utc" in v.message for v in violations)


def test_future_evidence_package_change_window_non_utc_fails_closed(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["change_window_utc"] = {
        "starts_at_utc": "2099-01-01T01:00:00+03:00",
        "ends_at_utc": "2099-01-01T02:00:00+03:00",
    }
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("change_window_utc.starts_at_utc must use UTC" in v.message for v in violations)


@pytest.mark.parametrize(
    "field", ["target_environment", "target_service", "target_version", "smoke_scope"]
)
def test_future_evidence_package_empty_core_string_field_fails_closed(
    tmp_path: Path, field: str
) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence[field] = " "
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any(f"{field} must be a non-empty string" in v.message for v in violations)


@pytest.mark.parametrize("field", ["rollback_owner", "emergency_disable_owner"])
def test_future_evidence_package_empty_owner_field_fails_closed(tmp_path: Path, field: str) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence[field] = ""
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any(f"{field} must be a non-empty string" in v.message for v in violations)


def test_future_evidence_package_empty_readiness_prerequisites_fails_closed(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["readiness_prerequisites"] = []
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("readiness_prerequisites" in v.message for v in violations)


def test_future_evidence_package_malformed_trace_correlation_fails_closed(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["trace_correlation"] = {
        "operation_id": "",
        "trace_id": "trace-1",
        "audit_event_refs": [],
    }
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("trace_correlation.operation_id" in v.message for v in violations)
    assert any("trace_correlation.audit_event_refs" in v.message for v in violations)


def test_future_evidence_package_weak_placeholder_values_fail_closed(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["target_environment"] = "TBD"
    evidence["rollback_owner"] = "placeholder"
    evidence["smoke_scope"] = "TODO"
    evidence["readiness_prerequisites"] = ["todo", "tbd", "placeholder"]
    evidence["trace_correlation"] = {
        "operation_id": "placeholder",
        "trace_id": "todo",
        "audit_event_refs": ["tbd"],
    }
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("target_environment must be a non-empty string" in v.message for v in violations)
    assert any("rollback_owner must be a non-empty string" in v.message for v in violations)
    assert any("smoke_scope must be a non-empty string" in v.message for v in violations)
    assert any("readiness_prerequisites" in v.message for v in violations)
    assert any("trace_correlation.operation_id" in v.message for v in violations)
    assert any("trace_correlation.trace_id" in v.message for v in violations)
    assert any("trace_correlation.audit_event_refs" in v.message for v in violations)


def test_future_evidence_package_near_future_generated_at_fails_closed(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    future = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=1)
    evidence["generated_at_utc"] = future.isoformat().replace("+00:00", "Z")
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("generated_at_utc must not be in the future" in v.message for v in violations)


def test_future_evidence_package_stale_change_window_fails_closed(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["change_window_utc"] = {
        "starts_at_utc": "2020-01-01T00:00:00Z",
        "ends_at_utc": "2020-01-01T01:00:00Z",
    }
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("change_window_utc is stale/expired" in v.message for v in violations)


@pytest.mark.parametrize(
    "field",
    [
        "operator_approval_ref",
        "security_approval_ref",
        "rollback_plan_ref",
        "emergency_disable_plan_ref",
        "redaction_report_ref",
        "independent_reviewer_ref",
    ],
)
def test_future_evidence_package_weak_reference_fields_fail_closed(
    tmp_path: Path, field: str
) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence[field] = "placeholder"
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any(f"{field} must be a non-empty strong reference" in v.message for v in violations)


@pytest.mark.parametrize(
    ("patch", "expected_field"),
    [
        ({"supplemental_evidence_ref": "redacted-followup-ref"}, "supplemental_evidence_ref"),
        (
            {"supplemental_evidence_refs": ["ticket-1", "placeholder-ticket"]},
            "supplemental_evidence_refs",
        ),
        ({"supplemental_reference": "placeholder-ticket-123"}, "supplemental_reference"),
        ({"approvalReferences": ["ticket-1", "todo-ticket-2"]}, "approvalReferences"),
        ({"nested": {"followup_ref": "todo-followup-ref"}}, "nested.followup_ref"),
        ({"nested": {"followupRefs": ["ticket-1", "example-ticket"]}}, "nested.followupRefs"),
    ],
)
def test_future_evidence_package_extra_weak_reference_fields_fail_closed(
    tmp_path: Path, patch: dict[str, object], expected_field: str
) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence.update(patch)
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any(
        f"{expected_field} must be a non-empty strong reference" in v.message for v in violations
    )


@pytest.mark.parametrize(
    "field",
    [
        "self_attestation_by_leader_ref",
        "self_attested_by_leader_ref",
        "self_review_by_implementer_ref",
        "implementer_review_ref",
    ],
)
def test_future_evidence_package_self_attestation_reference_keys_fail_closed(
    tmp_path: Path, field: str
) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence[field] = "ticket-123"
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("self-attestation" in v.message for v in violations)


def test_future_evidence_package_independent_reviewer_rejects_implementer_review_value(
    tmp_path: Path,
) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["independent_reviewer_ref"] = "implementer-review-ticket-123"
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any(
        "independent_reviewer_ref must be a non-empty strong reference" in v.message
        for v in violations
    )


def test_future_evidence_package_weak_marker_substrings_fail_closed(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["operator_approval_ref"] = "placeholder-approval-ref-123"
    evidence["security_approval_ref"] = "tbd-approval-ref-123"
    evidence["independent_reviewer_ref"] = "todo-reviewer-ref-123"
    evidence["target_environment"] = "TBD-production"
    evidence["redaction_report_ref"] = "redacted"
    evidence["rollback_plan_ref"] = "redacted-rollback-ref-123"
    evidence["target_service"] = "todo-service"
    evidence["smoke_scope"] = "TODO smoke checks"
    evidence["trace_correlation"] = {
        "operation_id": "placeholder-op-1",
        "trace_id": "todo-trace-1",
        "audit_event_refs": ["tbd-audit-1"],
    }
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any(
        "operator_approval_ref must be a non-empty strong reference" in v.message
        for v in violations
    )
    assert any(
        "security_approval_ref must be a non-empty strong reference" in v.message
        for v in violations
    )
    assert any(
        "redaction_report_ref must be a non-empty strong reference" in v.message for v in violations
    )
    assert any(
        "rollback_plan_ref must be a non-empty strong reference" in v.message for v in violations
    )
    assert any(
        "independent_reviewer_ref must be a non-empty strong reference" in v.message
        for v in violations
    )
    assert any("target_environment must be a non-empty string" in v.message for v in violations)
    assert any("target_service must be a non-empty string" in v.message for v in violations)
    assert any("smoke_scope must be a non-empty string" in v.message for v in violations)
    assert any("trace_correlation.operation_id" in v.message for v in violations)
    assert any("trace_correlation.trace_id" in v.message for v in violations)
    assert any("trace_correlation.audit_event_refs" in v.message for v in violations)


def test_future_evidence_package_started_change_window_fails_closed(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    now = datetime.now(UTC).replace(microsecond=0)
    evidence["change_window_utc"] = {
        "starts_at_utc": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "ends_at_utc": (now + timedelta(minutes=55)).isoformat().replace("+00:00", "Z"),
    }
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("change_window_utc has already started" in v.message for v in violations)


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Production activation is live for registry-api.",
        "Production activation is now active for registry-api.",
        "The controlled production cutover is live and serving traffic.",
    ],
)
def test_status_docs_activation_live_language_fails(tmp_path: Path, unsafe_text: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("activation overclaim" in v.message for v in violations)


@pytest.mark.parametrize(
    ("unsafe_text", "expected"),
    [
        (
            "Readiness artifacts demonstrate production activation in the target environment.",
            "readiness-as-proof",
        ),
        ("Plaintext fallback may be used during emergency rollback.", "plaintext fallback"),
        (
            "Self review by the implementer is sufficient independent reviewer evidence.",
            "self-attestation",
        ),
    ],
)
def test_status_docs_unsafe_synonym_language_fails(
    tmp_path: Path, unsafe_text: str, expected: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(expected in v.message for v in violations)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("activation_status", "production activation is live", "activation claim field"),
        ("production_activation_live", True, "activation claim field"),
        ("smoke_scope", "Production activation done.", "activation overclaim"),
        ("smoke_scope", "Go-live done.", "activation overclaim"),
        ("smoke_scope", "Cutover done.", "activation overclaim"),
        (
            "smoke_scope",
            "Production activation remains deferred, production activation is live.",
            "activation overclaim",
        ),
        (
            "smoke_scope",
            "The activation evidence schema/preflight gate records that production activation is live.",
            "activation overclaim",
        ),
        (
            "smoke_scope",
            "Production activation remains deferred, production activation live.",
            "activation overclaim",
        ),
        (
            "smoke_scope",
            "No live activation and activation completed successfully.",
            "activation overclaim",
        ),
        (
            "smoke_scope",
            "Without performing live activation, activation completed successfully.",
            "activation overclaim",
        ),
        (
            "smoke_scope",
            "Readiness artifacts demonstrate production activation in target env.",
            "readiness-as-proof",
        ),
        ("smoke_scope", "Plaintext fallback may be used during rollback.", "plaintext fallback"),
        (
            "independent_reviewer_ref",
            "self-review-by-implementer-sufficient-ref-123",
            "independent_reviewer_ref must be a non-empty strong reference",
        ),
    ],
)
def test_future_evidence_rejects_activation_claim_and_self_review_fields(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence[field] = value
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any(expected in v.message for v in violations)


def test_future_evidence_structured_secret_with_spaces_fails(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["db"] = {"password": "super secret prod password value 1234567890"}
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("structured secret" in v.message for v in violations)


@pytest.mark.parametrize(
    "patch",
    [
        {"db": {"password": ["abcdefghijklmnopqrstuvwxyz123456"]}},
        {"db": {"password": {"value": "abcdefghijklmnopqrstuvwxyz123456"}}},
    ],
)
def test_future_evidence_structured_secret_containers_fail(
    tmp_path: Path, patch: dict[str, object]
) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence.update(patch)
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("structured secret" in v.message for v in violations)


@pytest.mark.parametrize(
    "patch",
    [
        {"db": {"password": ["redacted"]}},
        {"db": {"password": {"value": "placeholder"}}},
    ],
)
def test_future_evidence_structured_secret_containers_allow_placeholders(
    tmp_path: Path, patch: dict[str, object]
) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence.update(patch)
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert not any("structured secret" in v.message for v in violations)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        (
            "smoke_scope",
            "Production activation" + chr(10) + "completed successfully.",
            "activation overclaim",
        ),
        (
            "smoke_scope",
            "Readiness artifacts" + chr(10) + "prove activation.",
            "readiness-as-proof",
        ),
        (
            "smoke_scope",
            "Plaintext fallback" + chr(10) + "is allowed during rollback.",
            "plaintext fallback",
        ),
    ],
)
def test_future_evidence_multiline_unsafe_language_fails(
    tmp_path: Path, field: str, value: str, expected: str
) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence[field] = value
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any(expected in v.message for v in violations)


@pytest.mark.parametrize(
    ("unsafe_text", "expected"),
    [
        ("The controlled production cutover completed successfully.", "activation overclaim"),
        ("Readiness artifacts certify go-live for the target environment.", "readiness-as-proof"),
        ("Readiness evidence established production cutover.", "readiness-as-proof"),
        ("Plaintext fallback is permitted during rollback.", "plaintext fallback"),
        ("Plaintext fallback can be used during rollback.", "plaintext fallback"),
        ("sslmode=disable is permitted during rollback.", "plaintext fallback"),
    ],
)
def test_status_docs_review_cycle_10_unsafe_language_fails(
    tmp_path: Path, unsafe_text: str, expected: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(expected in v.message for v in violations)


@pytest.mark.parametrize(
    "field",
    [
        "activation_occurred",
        "activation_successful",
        "activation_proof",
        "activation_done",
        "activation_serving_traffic",
        "production_activation_done",
        "cutover_successful",
        "cutover_proof",
        "cutover_done",
        "go_live_successful",
        "go_live_proof",
        "go_live_done",
    ],
)
def test_future_evidence_rejects_additional_activation_claim_fields(
    tmp_path: Path, field: str
) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence[field] = True
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("activation claim field" in v.message for v in violations)


@pytest.mark.parametrize(
    "patch",
    [
        {"activation": []},
        {"activation": {}},
        {"cutover": []},
        {"production_activation": {}},
        {"go_live": []},
        {"activation": {"performed": True}},
        {"activation": {"status": "live"}},
        {"activation": ["live"]},
        {"activation": ["succeeded"]},
        {"cutover": ["executed"]},
        {"activation": True},
        {"activation": "true"},
        {"activation": [True]},
        {"production_activation": True},
        {"cutover": True},
        {"activation": {"state": True}},
        {"activation": "yes"},
        {"go_live": "1"},
        {"cutover": ["yes"]},
        {"activation": {"state": "yes"}},
        {"activation_state": True},
        {"activation_state": "live"},
        {"activation_plan": True},
        {"production_activation_plan": "yes"},
        {"cutover_plan": ["yes"]},
        {"go_live_plan": {"state": "yes"}},
        {"activation_plan": ["ref-123"]},
        {"production_activation_plan": ["ticket-123"]},
        {"activation_plan": {"ref": "ticket-123"}},
        {"activation_plan": "manual-runbook-ref-123"},
        {"activation": {"ref": "ticket-123"}},
        {"activation": {"metadata": "manual-runbook-ref-123"}},
        {"go_live_plan": []},
        {"activation": {"done": True}},
        {"productionActivation": {"done": True}},
        {"cutover": {"successful": True}},
        {"cutover": {"done": True}},
        {"activation": {"serving_traffic": True}},
        {"activation": {"details": {"state": "live"}}},
        {"activation": [{"state": "live"}]},
        {"cutover": {"details": {"state": "serving traffic"}}},
        {"activation": {"state": "activated"}},
        {"cutover": {"state": "executed"}},
        {"go_live": {"state": "shipped"}},
        {"activation": {"details": {"state": "succeeded"}}},
        {"activation": {"state": "serving traffic"}},
        {"cutover": {"state": "successful completion"}},
        {"go_live": {"done": True}},
        {"activation": "done"},
        {"activation": "serving traffic"},
        {"activation": "live"},
        {"activation": "completed successfully"},
        {"production_activation": "completed"},
        {"production_activation": "completed successfully"},
        {"cutover": "done"},
        {"cutover": "serving traffic"},
        {"cutover": "successful completion"},
        {"production_cutover": "successful"},
    ],
)
def test_future_evidence_rejects_nested_activation_claim_objects(
    tmp_path: Path, patch: dict[str, object]
) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence.update(patch)
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("activation claim field" in v.message for v in violations)


def test_future_evidence_allows_explicit_future_subject_metadata(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["activation_plan"] = "future_operator_gated"
    evidence["cutover_plan"] = "deferred"
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert not any("activation claim field" in v.message for v in violations)


def test_future_evidence_allows_safe_go_live_subject_plan(tmp_path: Path) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence["go_live"] = {"planned": "future_operator_gated"}
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert not any("activation claim field" in v.message for v in violations)


@pytest.mark.parametrize(
    "patch",
    [
        {"db": {"sslmode": "disable"}},
        {"db.ssl": "disable"},
        {"db.ssl": ["disable"]},
        {"db": {"ssl": "disable"}},
        {"db": {"ssl": ["disabled"]}},
        {"db.ssl": False},
        {"db.ssl": "false"},
        {"db.ssl": "off"},
        {"db.ssl": [False]},
        {"db.ssl": ["false", "off"]},
        {"db.sslmode": False},
        {"db.sslmode": "off"},
        {"db.sslmode": [False, "off"]},
        {"db": {"ssl": False}},
        {"db": {"ssl": "false"}},
        {"db": {"ssl": [False, "off"]}},
        {"db": {"sslmode": False}},
        {"db": {"sslmode": "false"}},
        {"db": {"sslmode": [False, "off"]}},
        {"db": {"ssl": {"mode": "disable"}}},
        {"nested": {"ssl": {"mode": ["disabled"]}}},
        {"db.sslmode": "disable"},
        {"db.ssl_mode": ["disable"]},
        {"db.ssl": {"mode": "disable"}},
        {"db": {"sslmode": "disable during emergency rollback"}},
        {"db": {"sslmode": "disabled"}},
        {"db": {"sslMode": "disabled"}},
        {"db": {"ssl_mode": "disabled"}},
        {"db": {"sslmode": {"mode": "disabled"}}},
        {"db": {"sslmode": "allow"}},
        {"db": {"sslmode": "prefer"}},
        {"db": {"sslmode": "require"}},
        {"db": {"sslmode": "verify-ca"}},
        {"db": {"sslmode": {"mode": "verify-ca"}}},
        {"db": {"sslmode_allow": True}},
        {"db": {"sslmodeRequire": True}},
        {"db": {"sslMode": "disable"}},
        {"db": {"ssl_mode": "disable"}},
        {"nested": [{"sslMode": "disable"}]},
        {"plaintext_fallback": "permitted"},
        {"plaintext_fallback": "enabled for emergency rollback"},
        {"plainTextFallback": "may be used during rollback"},
        {"plaintext_fallback": True},
        {"db": {"sslMode": ["disable"]}},
        {"db": {"ssl_mode": ["disable"]}},
        {"db": {"ssl_mode_disable": True}},
        {"db": {"sslmode_disable": True}},
        {"db": {"ssl_mode_disabled": True}},
        {"db": {"sslmode": {"disabled": True}}},
        {"db": {"sslMode": {"mode": "disable"}}},
        {"plaintext_fallback_allowed": True},
        {"nested": {"plaintext_fallback_enabled": True}},
        {"plaintext_fallback": {"allowed": True}},
        {"nested": {"plaintext_fallback": {"enabled": "yes"}}},
        {"nested": {"plaintextFallbackAllowed": True}},
    ],
)
def test_future_evidence_structured_plaintext_fallback_fields_fail(
    tmp_path: Path, patch: dict[str, object]
) -> None:
    mod = _load_module()
    evidence = _valid_future_evidence(mod)
    evidence.update(patch)
    evidence_path = tmp_path / "future-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    violations = mod.validate_evidence_package(evidence_path)  # type: ignore[attr-defined]
    assert any("plaintext fallback" in v.message for v in violations)


@pytest.mark.parametrize(
    ("unsafe_text", "expected"),
    [
        ("Cutover completed successfully.", "activation overclaim"),
        (
            "Story 134.1 is complete locally as activation evidence and production activation completed successfully.",
            "activation overclaim",
        ),
        (
            "The activation evidence schema/preflight gate records that production activation completed successfully.",
            "activation overclaim",
        ),
        (
            "The activation evidence schema/preflight gate records completed production activation.",
            "activation overclaim",
        ),
        (
            "The activation evidence schema/preflight gate records that production activation is live.",
            "activation overclaim",
        ),
        (
            "The activation evidence schema/preflight gate records that production activation active.",
            "activation overclaim",
        ),
        (
            "The activation evidence schema/preflight gate records that production activation live.",
            "activation overclaim",
        ),
        (
            "Production activation remains deferred, production activation completed successfully.",
            "activation overclaim",
        ),
        (
            "Production activation remains deferred, production activation is live.",
            "activation overclaim",
        ),
        (
            "Production activation remains deferred, production activation is active.",
            "activation overclaim",
        ),
        (
            "Production activation remains deferred, production activation is serving traffic.",
            "activation overclaim",
        ),
        (
            "Production activation remains deferred, production activation active.",
            "activation overclaim",
        ),
        (
            "Production activation remains deferred, production activation live.",
            "activation overclaim",
        ),
        (
            "Production activation remains deferred, production activation serving traffic.",
            "activation overclaim",
        ),
        ("No live activation, completed production activation.", "activation overclaim"),
        ("No live activation and activation completed successfully.", "activation overclaim"),
        ("No live activation and activation is live.", "activation overclaim"),
        (
            "Without performing live activation, activation completed successfully.",
            "activation overclaim",
        ),
        (
            "This gate does not perform live activation and production activation completed successfully.",
            "activation overclaim",
        ),
        (
            "This gate runs without performing live activation and production activation completed successfully.",
            "activation overclaim",
        ),
        ("No live activation, cutover completed successfully.", "activation overclaim"),
        (
            "No live activation, controlled production cutover completed successfully.",
            "activation overclaim",
        ),
        ("ssl_mode=disable", "plaintext fallback"),
        ("ssl_mode:" + chr(10) + "disable", "plaintext fallback"),
        ("ssl mode disable", "plaintext fallback"),
        ('sslmode = "disable" is permitted during rollback', "plaintext fallback"),
        ("ssl-mode=`disable`", "plaintext fallback"),
        ("ssl_mode=disable is permitted during rollback.", "plaintext fallback"),
        ("ssl.mode=disable is permitted during rollback.", "plaintext fallback"),
        ("ssl_mode:" + chr(10) + "disable can be used during rollback.", "plaintext fallback"),
        ("ssl.mode:" + chr(10) + "disable can be used during rollback.", "plaintext fallback"),
        ("ssl mode disable is permitted during rollback.", "plaintext fallback"),
        ("Production activation done.", "activation overclaim"),
        ("Completed cutover.", "activation overclaim"),
        ("Completed go-live.", "activation overclaim"),
        ("Production activation is done.", "activation overclaim"),
        ("Cutover done.", "activation overclaim"),
        ("Controlled production cutover done.", "activation overclaim"),
        ("sslmode=disable may be used during rollback.", "plaintext fallback"),
        ("sslmode=false may be used during rollback.", "plaintext fallback"),
        ("sslmode=off is permitted during rollback.", "plaintext fallback"),
        ("sslmode=allow may be used during emergency rollback.", "plaintext fallback"),
        ("sslmode=prefer may be used during emergency rollback.", "plaintext fallback"),
        ("sslmode=require may be used during emergency rollback.", "plaintext fallback"),
        ("sslmode=verify-ca may be used during emergency rollback.", "plaintext fallback"),
        ("sslmode: disable" + chr(10) + "can be used during rollback.", "plaintext fallback"),
        ("sslmode: allow" + chr(10) + "can be used during rollback.", "plaintext fallback"),
        ("sslmode: false" + chr(10) + "can be used during rollback.", "plaintext fallback"),
        ("sslmode=disabled is permitted during rollback.", "plaintext fallback"),
        ("sslmode disable" + chr(10) + "is permitted during rollback.", "plaintext fallback"),
        ("sslmode:" + chr(10) + "disable can be used during rollback.", "plaintext fallback"),
        ("fallback to plaintext during rollback", "plaintext fallback"),
        ("sslmode=" + chr(10) + "disable is permitted during rollback.", "plaintext fallback"),
        ("sslmode disable is permitted during rollback.", "plaintext fallback"),
        ("sslmode: disable can be used during rollback.", "plaintext fallback"),
        ("Go-live done.", "activation overclaim"),
        ("Go-live completed successfully.", "activation overclaim"),
        ("Production activation" + chr(10) + "completed successfully.", "activation overclaim"),
        ("Go-live" + chr(10) + "completed successfully.", "activation overclaim"),
        ("Readiness artifacts" + chr(10) + "prove activation.", "readiness-as-proof"),
        ("Plaintext fallback" + chr(10) + "is allowed during rollback.", "plaintext fallback"),
    ],
)
def test_status_docs_review_cycle_11_unsafe_language_fails(
    tmp_path: Path, unsafe_text: str, expected: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    target = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    target.write_text(target.read_text(encoding="utf-8") + f"\n{unsafe_text}\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(expected in v.message for v in violations)


@pytest.mark.parametrize("rel_attr", ["JUSTFILE_PATH", "CI_PATH"])
def test_missing_checker_wiring_fails(tmp_path: Path, rel_attr: str) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    path = tmp_path / getattr(mod, rel_attr)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            mod.CHECKER_COMMAND,
            "uv run python scripts/other_checker.py",  # type: ignore[attr-defined]
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert violations


def test_missing_ci_live_checker_step_fails_even_when_self_test_remains(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    path = tmp_path / mod.CI_PATH  # type: ignore[attr-defined]
    lines = path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    replaced = False
    for line in lines:
        if (
            not replaced
            and mod.CHECKER_COMMAND in line  # type: ignore[attr-defined]
            and mod.CHECKER_SELF_TEST_COMMAND not in line  # type: ignore[attr-defined]
        ):
            updated.append(
                line.replace(
                    mod.CHECKER_COMMAND,  # type: ignore[attr-defined]
                    "uv run python scripts/other_checker.py",
                )
            )
            replaced = True
        else:
            updated.append(line)
    assert replaced
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(
        "CI static checks must run controlled activation checker" in v.message for v in violations
    )


@pytest.mark.parametrize("recipe", ["lint", "check-gates"])
def test_missing_just_live_checker_step_fails_even_when_self_test_remains(
    tmp_path: Path, recipe: str
) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    path = tmp_path / mod.JUSTFILE_PATH  # type: ignore[attr-defined]
    lines = path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    in_recipe = False
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped == f"{recipe}:":
            in_recipe = True
        elif in_recipe and stripped and not line.startswith((" ", "\t")):
            in_recipe = False
        if in_recipe and stripped == mod.CHECKER_COMMAND and not replaced:  # type: ignore[attr-defined]
            updated.append(
                line.replace(
                    mod.CHECKER_COMMAND,  # type: ignore[attr-defined]
                    mod.CHECKER_SELF_TEST_COMMAND,  # type: ignore[attr-defined]
                )
            )
            replaced = True
        else:
            updated.append(line)
    assert replaced
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any(f"{recipe} must run controlled activation checker" in v.message for v in violations)
