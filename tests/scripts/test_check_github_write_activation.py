"""Tests for Story 131.3 GitHub write activation readiness gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_github_write_activation.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_github_write_activation", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_github_write_activation"] = mod
    spec.loader.exec_module(mod)
    return mod


def _copy_live_fixture(tmp_path: Path, mod: object) -> None:
    for rel in [
        mod.CONTRACT_PATH,  # type: ignore[attr-defined]
        mod.CREDENTIAL_CONTRACT_PATH,  # type: ignore[attr-defined]
        mod.GITHUB_REST_PATH,  # type: ignore[attr-defined]
        mod.GITHUB_SERVER_PATH,  # type: ignore[attr-defined]
        mod.GITHUB_TOOLS_PATH,  # type: ignore[attr-defined]
        Path("docs/production-operations.md"),
        Path("docs/operator-runbook.md"),
        Path("docs/feature-status.md"),
        Path("_bmad-output/implementation-artifacts/131-3-github-write-activation-readiness.md"),
    ]:
        src = REPO_ROOT / rel
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def test_self_test_passes() -> None:
    mod = _load_module()
    assert mod._self_test() == 0  # type: ignore[attr-defined]


def test_live_contract_is_clean() -> None:
    mod = _load_module()
    assert mod.main([]) == 0  # type: ignore[attr-defined]


def test_build_server_simulate_false_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    server = tmp_path / mod.GITHUB_SERVER_PATH  # type: ignore[attr-defined]
    server.write_text(
        server.read_text(encoding="utf-8").replace(
            "GitHubWriteClient(scoped_token=scoped_token)",
            "GitHubWriteClient(scoped_token=scoped_token, simulate=False)",
            1,
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("simulate=False" in v.message for v in violations)


def test_write_tool_tier_downgrade_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    tools = tmp_path / mod.GITHUB_TOOLS_PATH  # type: ignore[attr-defined]
    tools.write_text(
        tools.read_text(encoding="utf-8").replace(
            '"github.issues.create": Tier.THREE,',
            '"github.issues.create": Tier.ONE,',
            1,
        ),
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("github.issues.create must be Tier.THREE" in v.message for v in violations)


def test_runtime_activation_flag_read_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    server = tmp_path / mod.GITHUB_SERVER_PATH  # type: ignore[attr-defined]
    server.write_text(
        server.read_text(encoding="utf-8")
        + "\n# forbidden runtime gate: GITHUB_MCP_WRITE_ENABLED\n",
        encoding="utf-8",
    )
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("GITHUB_MCP_WRITE_ENABLED" in v.message for v in violations)


def test_contract_missing_required_evidence_fails(tmp_path: Path) -> None:
    mod = _load_module()
    _copy_live_fixture(tmp_path, mod)
    contract = tmp_path / mod.CONTRACT_PATH  # type: ignore[attr-defined]
    data = json.loads(contract.read_text(encoding="utf-8"))
    data["required_evidence"] = [x for x in data["required_evidence"] if "emergency" not in x]
    contract.write_text(json.dumps(data), encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("required_evidence missing" in v.message for v in violations)
