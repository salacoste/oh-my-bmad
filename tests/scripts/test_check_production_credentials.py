"""Tests for Story 131.2 production credential readiness gate."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_production_credentials.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_production_credentials", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_production_credentials"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_self_test_passes() -> None:
    mod = _load_module()
    assert mod._self_test() == 0  # type: ignore[attr-defined]


def test_live_inventory_is_clean() -> None:
    mod = _load_module()
    assert mod.main([]) == 0  # type: ignore[attr-defined]


def test_broad_env_var_in_inventory_fails(tmp_path: Path) -> None:
    mod = _load_module()
    for rel in [
        mod.INVENTORY_PATH,  # type: ignore[attr-defined]
        mod.WORKER_MCP_CLIENTS,  # type: ignore[attr-defined]
        mod.ORCH_MCP_CLIENTS,  # type: ignore[attr-defined]
        mod.WORKER_CLAUDE_RUNNER,  # type: ignore[attr-defined]
        mod.WORKER_CODEX_RUNNER,  # type: ignore[attr-defined]
        mod.WORKER_GEMINI_RUNNER,  # type: ignore[attr-defined]
        mod.ORCH_OMC_RUNNER,  # type: ignore[attr-defined]
        Path("docs/operator-runbook.md"),
        Path("docs/production-operations.md"),
        Path("docs/feature-status.md"),
    ]:
        src = REPO_ROOT / rel
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.copy2(src, dst)
        else:
            dst.write_text("placeholder\n", encoding="utf-8")
    data = json.loads((tmp_path / mod.INVENTORY_PATH).read_text(encoding="utf-8"))  # type: ignore[attr-defined]
    data["credentials"][0]["env_var"] = "GITHUB_TOKEN"
    (tmp_path / mod.INVENTORY_PATH).write_text(json.dumps(data), encoding="utf-8")  # type: ignore[attr-defined]
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("forbidden broad secret" in v.message for v in violations)


def test_scoped_token_in_non_github_server_fails(tmp_path: Path) -> None:
    mod = _load_module()
    for rel in [
        mod.INVENTORY_PATH,  # type: ignore[attr-defined]
        mod.WORKER_MCP_CLIENTS,  # type: ignore[attr-defined]
        mod.ORCH_MCP_CLIENTS,  # type: ignore[attr-defined]
        mod.WORKER_CLAUDE_RUNNER,  # type: ignore[attr-defined]
        mod.WORKER_CODEX_RUNNER,  # type: ignore[attr-defined]
        mod.WORKER_GEMINI_RUNNER,  # type: ignore[attr-defined]
        mod.ORCH_OMC_RUNNER,  # type: ignore[attr-defined]
        Path("docs/operator-runbook.md"),
        Path("docs/production-operations.md"),
        Path("docs/feature-status.md"),
    ]:
        src = REPO_ROOT / rel
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    worker = tmp_path / mod.WORKER_MCP_CLIENTS  # type: ignore[attr-defined]
    text = worker.read_text(encoding="utf-8")
    text = text.replace(
        '"GIT_MCP_WORKTREE_ROOT",\n        }\n    ),',
        '"GIT_MCP_WORKTREE_ROOT",\n            "GITHUB_MCP_SCOPED_TOKEN",\n        }\n    ),',
    )
    worker.write_text(text, encoding="utf-8")
    violations = mod.validate(tmp_path)  # type: ignore[attr-defined]
    assert any("non-authorized server 'git'" in v.message for v in violations)
