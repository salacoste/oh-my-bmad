"""Unit tests for scripts/check_trace_id_required.py (NFR-O7).

Covers:
  - Self-test harness exits 0 on bundled fixtures.
  - Clean fixture (every call passes trace_id= / suppressed) → exit 0.
  - Violation fixture (calls omit trace_id=) → exit 1.
  - The live first-party tree is clean (regression guard for the gate itself).
  - A **kwargs splat is treated as possibly carrying trace_id (fail-open).

NO ``slow`` marker — must run in the PR-gate ``pytest -m "not slow"`` lane.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_trace_id_required.py"
FIXTURES = REPO_ROOT / "scripts" / "checks" / "fixtures" / "trace_id_required"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_trace_id_required", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_trace_id_required"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_self_test_passes() -> None:
    mod = _load_module()
    assert mod._self_test() == 0  # type: ignore[attr-defined]


def test_clean_fixture_yields_no_violations() -> None:
    mod = _load_module()
    fixture = FIXTURES / "clean" / "with_trace_id.py"
    assert mod._scan_file(fixture) == []  # type: ignore[attr-defined]


def test_violation_fixture_yields_violations() -> None:
    mod = _load_module()
    fixture = FIXTURES / "violations" / "missing_trace_id.py"
    viols = mod._scan_file(fixture)  # type: ignore[attr-defined]
    # Both the bare-name and attribute-chain missing-trace_id calls fire.
    assert len(viols) == 2
    assert all(v.rule == "TRACE001" for v in viols)


def test_scan_clean_dir_exit_zero() -> None:
    mod = _load_module()
    viols, scanned = mod._scan([FIXTURES / "clean"])  # type: ignore[attr-defined]
    assert viols == []
    assert scanned >= 1


def test_scan_violations_dir_exit_one() -> None:
    mod = _load_module()
    viols, scanned = mod._scan([FIXTURES / "violations"])  # type: ignore[attr-defined]
    assert len(viols) >= 1
    assert scanned >= 1


def test_real_tree_is_clean() -> None:
    """Regression guard: every live EventEnvelope.create call passes trace_id=."""
    mod = _load_module()
    assert mod.main([]) == 0  # type: ignore[attr-defined]


def test_kwargs_splat_is_fail_open(tmp_path: Path) -> None:
    """A **kwargs splat may statically-unresolvably carry trace_id → not flagged."""
    mod = _load_module()
    f = tmp_path / "splat.py"
    f.write_text(
        "def emit(p):\n    return EventEnvelope.create(**p)\n",
        encoding="utf-8",
    )
    assert mod._scan_file(f) == []  # type: ignore[attr-defined]


def test_explicit_trace_id_keyword_is_clean(tmp_path: Path) -> None:
    mod = _load_module()
    f = tmp_path / "ok.py"
    f.write_text(
        "def emit(t):\n    return EventEnvelope.create(type='x', trace_id=t)\n",
        encoding="utf-8",
    )
    assert mod._scan_file(f) == []  # type: ignore[attr-defined]


def test_missing_trace_id_keyword_fires(tmp_path: Path) -> None:
    mod = _load_module()
    f = tmp_path / "bad.py"
    f.write_text(
        "def emit():\n    return EventEnvelope.create(type='x')\n",
        encoding="utf-8",
    )
    viols = mod._scan_file(f)  # type: ignore[attr-defined]
    assert len(viols) == 1
    assert viols[0].rule == "TRACE001"


def test_unrelated_create_call_not_flagged(tmp_path: Path) -> None:
    """A ``.create(...)`` on some other object must NOT fire (precision)."""
    mod = _load_module()
    f = tmp_path / "other.py"
    f.write_text(
        "def emit(repo):\n    return repo.create(name='x')\n",
        encoding="utf-8",
    )
    assert mod._scan_file(f) == []  # type: ignore[attr-defined]


def test_noqa_suppression(tmp_path: Path) -> None:
    mod = _load_module()
    f = tmp_path / "suppressed.py"
    f.write_text(
        "def emit():\n    return EventEnvelope.create(type='x')  # noqa: TRACE001 reviewed\n",
        encoding="utf-8",
    )
    assert mod._scan_file(f) == []  # type: ignore[attr-defined]
