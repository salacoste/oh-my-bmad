"""Unit tests for scripts/check_registry_isolation.py (Story 8.7.5 PP3).

Covers:
  - Self-test harness exits 0 on bundled fixtures.
  - The PP1 fix in services/registry-state/src/registry_state/test_event_log.py
    is detected as compliant (regression test for the original bug).
  - The packages/secret-hygiene test file is detected as compliant.
  - A synthetic bad-teardown fixture triggers RI001.
  - noqa: RI001 suppresses violations.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_registry_isolation.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_registry_isolation", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_registry_isolation"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_self_test_passes() -> None:
    """The bundled fixtures all classify correctly."""
    mod = _load_module()
    assert mod._self_test() == 0  # type: ignore[attr-defined]


def test_main_clean_run() -> None:
    """A clean run over the real repo must exit 0 after PP1/PP2 fixes."""
    mod = _load_module()
    assert mod.main([]) == 0  # type: ignore[attr-defined]


def test_pp1_fixed_file_compliant(tmp_path: Path) -> None:
    """Regression: ensure the PP1 fix in test_event_log.py is detected as compliant."""
    mod = _load_module()
    target = (
        REPO_ROOT / "services" / "registry-state" / "src" / "registry_state" / "test_event_log.py"
    )
    assert target.exists()
    violations, _ = mod._scan([target])  # type: ignore[attr-defined]
    assert violations == [], (
        f"PP1-fixed file should not violate RI001 but got: {[str(v) for v in violations]}"
    )


def test_secret_hygiene_compliant() -> None:
    """packages/secret-hygiene test_audited_secret.py should be RI001-clean after PP2."""
    mod = _load_module()
    target = (
        REPO_ROOT
        / "packages"
        / "secret-hygiene"
        / "src"
        / "secret_hygiene"
        / "test_audited_secret.py"
    )
    assert target.exists()
    violations, _ = mod._scan([target])  # type: ignore[attr-defined]
    assert violations == [], (
        f"PP2-fixed file should not violate RI001 but got: {[str(v) for v in violations]}"
    )


def test_noqa_suppression(tmp_path: Path) -> None:
    """A ``# noqa: RI001 <reason>`` comment must suppress the violation."""
    mod = _load_module()
    src = textwrap.dedent(
        """
        from events.schema_registry import unregister_all

        def teardown():
            unregister_all()  # noqa: RI001 — intentional, see docs/testing-guide.md
        """
    ).strip()
    p = tmp_path / "leaky_with_noqa.py"
    p.write_text(src)
    violations, _ = mod._scan([p])  # type: ignore[attr-defined]
    assert violations == []


def test_bad_teardown_flagged(tmp_path: Path) -> None:
    """A test fixture that calls unregister_all() without restore must be flagged."""
    mod = _load_module()
    src = textwrap.dedent(
        """
        from events.schema_registry import unregister_all

        def teardown():
            unregister_all()
        """
    ).strip()
    p = tmp_path / "leaky.py"
    p.write_text(src)
    violations, _ = mod._scan([p])  # type: ignore[attr-defined]
    assert len(violations) == 1
    assert violations[0].rule == "RI001"


def test_module_level_unregister_flagged(tmp_path: Path) -> None:
    """A module-level unregister_all() call must be flagged."""
    mod = _load_module()
    src = textwrap.dedent(
        """
        from events.schema_registry import unregister_all
        unregister_all()
        """
    ).strip()
    p = tmp_path / "module_level.py"
    p.write_text(src)
    violations, _ = mod._scan([p])  # type: ignore[attr-defined]
    assert len(violations) == 1
    assert "module scope" in violations[0].message


def test_ast_helpers() -> None:
    """Sanity checks on the AST predicates."""
    mod = _load_module()
    tree = ast.parse(
        "unregister_all()\nsr.unregister_all()\nensure_registered()\nregister('x','1','y')\n"
    )
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert mod._is_unregister_all_call(calls[0])  # type: ignore[attr-defined]
    assert mod._is_unregister_all_call(calls[1])  # type: ignore[attr-defined]
    assert mod._is_ensure_registered_call(calls[2])  # type: ignore[attr-defined]
    assert mod._is_register_call(calls[3])  # type: ignore[attr-defined]
