"""Tests for Story 134.2 split-deployment activation smoke evidence checker."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_split_deployment_activation_smoke_evidence.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "check_split_deployment_activation_smoke_evidence", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_split_deployment_activation_smoke_evidence"] = mod
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
    domains.pop("event_log_append_authority")
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
        "Split deployment activation is live.",
        "The production activation completed successfully.",
        "Go-live completed for split deployment.",
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
        + "\nReadiness artifacts prove activation for split deployment.\n",
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
    assert any("lint must run Story 134.2 checker" in v.message for v in violations)


def test_story_status_backlog_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    status = tmp_path / mod.SPRINT_STATUS_PATH  # type: ignore[attr-defined]
    status.write_text(
        status.read_text(encoding="utf-8").replace(
            "134-2-split-deployment-activation-smoke-evidence-package: done",
            "134-2-split-deployment-activation-smoke-evidence-package: backlog",
            1,
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("Story 134.2 must be done/closed" in v.message for v in violations)


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
    feature = tmp_path / mod.FEATURE_STATUS_PATH  # type: ignore[attr-defined]
    feature.write_text(
        feature.read_text(encoding="utf-8").replace(
            "split-deployment activation smoke evidence package",
            "split-deployment smoke evidence package",
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert not violations


def test_secret_like_contract_material_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    data = _load_contract(tmp_path, mod)
    data["unsafe_secret"] = "password=super-secret-value"
    _write_contract(tmp_path, mod, data)
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("secret-like" in v.message for v in violations)
