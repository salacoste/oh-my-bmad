"""Unit tests for scripts/mutation_score.py (Story 14.2 / NFR-O11).

Proves the score computation + ``--threshold`` exit logic against FIXTURE
cosmic-ray ``dump`` output — WITHOUT running cosmic-ray. The fixtures mirror the
real ``[WorkItem, WorkResult]``-per-line contract captured from the tiers.py
smoke (worker_outcome / test_outcome axes). Cases:

  - all-killed              -> score 100% + exit 0 (no threshold)
  - some-survived + --threshold above score -> exit 1
  - score == threshold boundary            -> exit 0 (>= is inclusive)
  - no-test / skipped excluded from denominator (coverage gaps)
  - exception / incompetent count as checked-but-not-killed (conservative)
  - null WorkResult (pending) skipped
  - missing dump file                       -> exit 1 (fail-closed)
  - stdin ('--dump-path -') reads the dump from stdin (no subprocess)

Mirrors the module-load convention in scripts/test_check_sbom_licenses.py.

NO ``slow`` marker — must run in the PR-gate ``pytest -m "not slow"`` lane.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "mutation_score.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("mutation_score", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mutation_score"] = mod
    spec.loader.exec_module(mod)
    return mod


def _item(occurrence: int = 0) -> dict[str, object]:
    """A minimal WorkItem stub (only shape matters; content is ignored)."""
    return {"job_id": f"job{occurrence}", "mutations": []}


def _result(
    *, worker_outcome: str = "normal", test_outcome: str | None = None
) -> dict[str, object]:
    """A minimal WorkResult stub mirroring cosmic-ray's two outcome axes."""
    r: dict[str, object] = {"worker_outcome": worker_outcome, "output": ""}
    if test_outcome is not None:
        r["test_outcome"] = test_outcome
    return r


def _dump_lines(pairs: list[list[object]]) -> str:
    """Render pairs as cosmic-ray dump text (one JSON list per line)."""
    return "\n".join(json.dumps(p) for p in pairs) + "\n"


def _write_dump(tmp_path: Path, pairs: list[list[object]]) -> Path:
    p = tmp_path / "dump.txt"
    p.write_text(_dump_lines(pairs), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# compute_score / format_score_line (pure seam)
# --------------------------------------------------------------------------- #


def test_compute_score_all_killed() -> None:
    mod = _load_module()
    pairs = [(_item(i), _result(test_outcome="killed")) for i in range(10)]
    score = mod.compute_score(pairs)
    assert score.killed == 10
    assert score.checked == 10
    assert score.pct == 100.0
    assert mod.format_score_line(score) == "mutation-score: 10/10 = 100.0%"


def test_compute_score_some_survived() -> None:
    mod = _load_module()
    pairs = [(_item(i), _result(test_outcome="killed")) for i in range(8)]
    pairs += [(_item(i), _result(test_outcome="survived")) for i in range(2)]
    score = mod.compute_score(pairs)
    assert score.killed == 8
    assert score.checked == 10
    assert score.pct == 80.0
    assert mod.format_score_line(score) == "mutation-score: 8/10 = 80.0%"


def test_compute_score_exception_and_incompetent_count_as_not_killed() -> None:
    mod = _load_module()
    pairs = [(_item(i), _result(test_outcome="killed")) for i in range(5)]
    pairs += [(_item(0), _result(test_outcome="survived"))]
    pairs += [(_item(0), _result(worker_outcome="exception"))]
    pairs += [(_item(0), _result(worker_outcome="abnormal"))]
    pairs += [(_item(0), _result(worker_outcome="normal", test_outcome="incompetent"))]
    pairs += [(_item(0), _result(test_outcome="killed"))]  # one more kill -> 6
    score = mod.compute_score(pairs)
    # killed=6, checked=6 + survived(1) + exception(1) + abnormal(1) + incompetent(1) = 10
    assert score.killed == 6
    assert score.checked == 10
    assert score.pct == 60.0


def test_compute_score_excludes_no_test_and_skipped_from_denominator() -> None:
    mod = _load_module()
    # no-test / skipped measure coverage gaps, not suite weakness — excluded.
    pairs = [(_item(i), _result(test_outcome="killed")) for i in range(4)]
    pairs += [(_item(0), _result(worker_outcome="no-test"))]
    pairs += [(_item(0), _result(worker_outcome="skipped"))]
    score = mod.compute_score(pairs)
    assert score.checked == 4
    assert score.killed == 4
    assert score.pct == 100.0


def test_compute_score_null_result_skipped() -> None:
    mod = _load_module()
    pairs: list[tuple[dict[str, object], dict[str, object] | None]] = [
        (_item(0), _result(test_outcome="killed")),
        (_item(1), None),  # pending / incomplete — not counted
    ]
    score = mod.compute_score(pairs)
    assert score.killed == 1
    assert score.checked == 1
    assert score.pct == 100.0


def test_compute_score_zero_checked_is_zero_pct_not_crash() -> None:
    mod = _load_module()
    pairs: list[tuple[dict[str, object], dict[str, object] | None]] = [(_item(0), None)]
    score = mod.compute_score(pairs)
    assert score.checked == 0
    assert score.pct == 0.0


# --------------------------------------------------------------------------- #
# parse_dump_text
# --------------------------------------------------------------------------- #


def test_parse_dump_text_round_trip() -> None:
    mod = _load_module()
    text = _dump_lines(
        [
            [_item(0), _result(test_outcome="killed")],
            [_item(1), None],
        ]
    )
    pairs = mod.parse_dump_text(text)
    assert len(pairs) == 2
    assert pairs[1][1] is None


def test_parse_dump_text_ignores_blank_lines() -> None:
    mod = _load_module()
    text = "\n\n" + json.dumps([_item(0), _result(test_outcome="killed")]) + "\n\n"
    pairs = mod.parse_dump_text(text)
    assert len(pairs) == 1


def test_parse_dump_text_rejects_bad_shape() -> None:
    mod = _load_module()
    with pytest.raises(ValueError):
        mod.parse_dump_text(json.dumps([{"only": "one element"}]) + "\n")


# --------------------------------------------------------------------------- #
# main() exit-code behavior with --threshold (via --dump-path)
# --------------------------------------------------------------------------- #


def test_main_all_killed_no_threshold_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _load_module()
    dump = _write_dump(tmp_path, [[_item(i), _result(test_outcome="killed")] for i in range(7)])
    rc = mod.main(["--dump-path", str(dump)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "mutation-score: 7/7 = 100.0%" in out


def test_main_below_threshold_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_module()
    pairs = [[_item(i), _result(test_outcome="killed")] for i in range(6)]
    pairs += [[_item(i), _result(test_outcome="survived")] for i in range(4)]  # 60%
    dump = _write_dump(tmp_path, pairs)
    rc = mod.main(["--dump-path", str(dump), "--threshold", "80"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAIL" in err


def test_main_at_threshold_boundary_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _load_module()
    pairs = [[_item(i), _result(test_outcome="killed")] for i in range(8)]
    pairs += [[_item(i), _result(test_outcome="survived")] for i in range(2)]  # exactly 80%
    dump = _write_dump(tmp_path, pairs)
    rc = mod.main(["--dump-path", str(dump), "--threshold", "80"])
    assert rc == 0  # >= is inclusive — boundary passes
    out = capsys.readouterr().out
    assert "OK" in out


def test_main_above_threshold_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _load_module()
    pairs = [[_item(i), _result(test_outcome="killed")] for i in range(9)]
    pairs += [[_item(0), _result(test_outcome="survived")]]  # 90%
    dump = _write_dump(tmp_path, pairs)
    rc = mod.main(["--dump-path", str(dump), "--threshold", "80"])
    assert rc == 0


def test_main_score_at_or_above_82_threshold_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Story 14.3: the production gate threshold is 82 (= floor of the 82.4%
    # baseline). A fixture scoring >= 82% must pass the gate (exit 0).
    # 41/50 = 82.0% — exactly the boundary; >= is inclusive so it passes.
    mod = _load_module()
    pairs = [[_item(i), _result(test_outcome="killed")] for i in range(41)]
    pairs += [[_item(i), _result(test_outcome="survived")] for i in range(9)]  # 41/50 = 82.0%
    dump = _write_dump(tmp_path, pairs)
    rc = mod.main(["--dump-path", str(dump), "--threshold", "82"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_main_score_below_82_threshold_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Story 14.3: a fixture scoring < 82% must FAIL the gate (exit 1) — this is
    # the regression the nightly mutation-gate job is wired to catch.
    # 40/50 = 80.0% — below the 82 floor.
    mod = _load_module()
    pairs = [[_item(i), _result(test_outcome="killed")] for i in range(40)]
    pairs += [[_item(i), _result(test_outcome="survived")] for i in range(10)]  # 40/50 = 80.0%
    dump = _write_dump(tmp_path, pairs)
    rc = mod.main(["--dump-path", str(dump), "--threshold", "82"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAIL" in err


def test_main_missing_dump_file_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _load_module()
    missing = tmp_path / "does-not-exist.txt"
    rc = mod.main(["--dump-path", str(missing)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err


def test_main_threshold_with_zero_checked_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _load_module()
    dump = _write_dump(tmp_path, [[_item(0), None]])
    rc = mod.main(["--dump-path", str(dump), "--threshold", "50"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no mutants" in err


def test_main_reads_dump_from_stdin(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_module()
    text = _dump_lines([[_item(i), _result(test_outcome="killed")] for i in range(5)])
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    rc = mod.main(["--dump-path", "-"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "mutation-score: 5/5 = 100.0%" in out


def test_main_malformed_json_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_module()
    bad = tmp_path / "dump.txt"
    bad.write_text("{not valid json\n", encoding="utf-8")
    rc = mod.main(["--dump-path", str(bad)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "failed to parse" in err
