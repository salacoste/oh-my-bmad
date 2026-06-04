#!/usr/bin/env python3
"""mutation_score.py — compute the cosmic-ray mutation score (Story 14.2, NFR-O11).

Reads pre-captured ``cosmic-ray dump`` output (the stable machine contract: one
JSON ``[WorkItem, WorkResult]`` pair per line), computes ``killed / checked``
and prints a single score line, e.g.::

    mutation-score: 68/90 = 75.6%

This script is PURE parse + score: it never spawns a subprocess. The caller (a
``just`` recipe / CI) is responsible for running ``cosmic-ray dump <session>``
and feeding the text in via ``--dump-path PATH`` or stdin (``--dump-path -``).

WHY cosmic-ray (not mutmut)? mutmut copies sources into ``mutants/`` but this
workspace's *editable* (uv-workspace) installs resolve imports back to the
pristine real source, so no mutant was ever exercised → a false 0% (every
mutant "survived"). cosmic-ray mutates the real source file IN PLACE under VCS
during ``exec`` and restores it afterward, so editable installs pick up each
mutation. Proven: the tiers.py smoke yields a real 68/90 kill, not 0/90.

SCOPE (Story 14.2): this is the unit-testable scoring seam + an *optional*
``--threshold`` comparator. The threshold flag is BUILT now but NOT wired into
any CI gate yet — Story 14.3 wires enforcement once the first nightly baseline
establishes a defensible floor. Until then the nightly ``mutation-baseline``
job runs this WITHOUT ``--threshold`` (informational only / non-gating).

INPUT FORMATS (one seam, two ways to feed it — neither spawns a process):
  - ``--dump-path PATH``: a pre-captured ``cosmic-ray dump`` text file (one
                          ``[WorkItem, WorkResult]`` JSON list per line). Lets
                          CI capture the dump once and lets tests feed fixtures
                          without a real session.
  - ``--dump-path -``   : read the same dump text from stdin (e.g.
                          ``cosmic-ray dump <session> | python mutation_score.py --dump-path -``).

Mutation-score denominator (NFR-O11 convention): a mutant counts toward the
denominator only if it was actually *checked* by the suite. cosmic-ray records
two outcome axes per result — ``worker_outcome`` (did the worker run?) and
``test_outcome`` (did the test kill it?). We map them as::

    killed   : test_outcome == "killed"
    survived : test_outcome == "survived"
    ambiguous: worker_outcome in {exception, abnormal}  OR
               test_outcome == "incompetent"
               -> counted as CHECKED but NOT killed (conservative)
    excluded : worker_outcome in {no-test, skipped}     (coverage gaps)

    checked  = killed + survived + ambiguous
    score    = killed / checked

``no-test`` / ``skipped`` measure coverage gaps, not test-suite weakness, and
folding them in would conflate the two signals — so they are EXCLUDED from the
denominator. ``exception`` / ``abnormal`` / ``incompetent`` are ambiguous
worker results; conservatively they count against the score (not a clean kill).

Stdlib-only + mypy-clean by design (mirrors the sibling CI-gate scripts).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NamedTuple

# Worker outcomes that mean the worker never produced a usable verdict because
# no test covered the mutated line / the job was skipped — coverage gaps,
# excluded from the score denominator.
_EXCLUDED_WORKER_OUTCOMES = frozenset({"no-test", "skipped"})
# Worker outcomes that mean the worker itself misbehaved (crash / exception).
# Ambiguous: counted as checked-but-not-killed (conservative).
_AMBIGUOUS_WORKER_OUTCOMES = frozenset({"exception", "abnormal"})


class Score(NamedTuple):
    """A computed mutation score.

    ``killed`` / ``checked`` are the numerator / denominator; ``pct`` is the
    percentage (0.0 when nothing was checked, to avoid a divide-by-zero while
    still emitting a deterministic line).
    """

    killed: int
    checked: int
    pct: float


def compute_score(results: list[tuple[dict[str, object], dict[str, object] | None]]) -> Score:
    """Compute the mutation score from parsed cosmic-ray dump pairs.

    The pure, unit-testable seam. Accepts the list of ``(work_item, work_result)``
    pairs that ``cosmic-ray dump`` emits (``work_result`` is ``None`` for a
    WorkItem with no result — e.g. a pending/incomplete job — and is skipped).
    """
    killed = 0
    checked = 0

    for _item, result in results:
        if result is None:
            continue  # no result yet (pending / incomplete) — not counted

        worker_outcome = _as_str(result.get("worker_outcome"))
        test_outcome = _as_str(result.get("test_outcome"))

        if worker_outcome in _EXCLUDED_WORKER_OUTCOMES:
            continue  # coverage gap — excluded from denominator

        if test_outcome == "killed":
            killed += 1
            checked += 1
        elif test_outcome == "survived":
            checked += 1
        elif worker_outcome in _AMBIGUOUS_WORKER_OUTCOMES or test_outcome == "incompetent":
            # crash / exception / incompetent mutant: ambiguous, conservatively
            # counted as checked-but-not-killed.
            checked += 1
        # Any other shape (e.g. a normal worker with no test_outcome) is treated
        # as uncounted rather than silently inflating the denominator.

    pct = (killed / checked * 100.0) if checked else 0.0
    return Score(killed=killed, checked=checked, pct=pct)


def _as_str(value: object) -> str:
    """Coerce a JSON value to a string for outcome comparison (None -> '')."""
    return value if isinstance(value, str) else ""


def format_score_line(score: Score) -> str:
    """Render the canonical one-line score string."""
    return f"mutation-score: {score.killed}/{score.checked} = {score.pct:.1f}%"


def parse_dump_text(text: str) -> list[tuple[dict[str, object], dict[str, object] | None]]:
    """Parse ``cosmic-ray dump`` output (one JSON list per line).

    Each non-blank line is a 2-element JSON list ``[WorkItem, WorkResult]``;
    ``WorkResult`` may be ``null``. Blank lines are ignored.
    """
    pairs: list[tuple[dict[str, object], dict[str, object] | None]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, list) or len(record) != 2:
            raise ValueError(
                f"expected a 2-element [WorkItem, WorkResult] list, got: {line[:80]!r}"
            )
        item, result = record
        if not isinstance(item, dict):
            raise ValueError(f"expected WorkItem to be an object, got {type(item).__name__}")
        if result is not None and not isinstance(result, dict):
            raise ValueError(
                f"expected WorkResult to be an object or null, got {type(result).__name__}"
            )
        pairs.append((item, result))
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compute the cosmic-ray mutation score from a pre-captured "
            "`cosmic-ray dump` file (or stdin), and print a "
            "'mutation-score: killed/checked = pct%' line. Never spawns a "
            "process. Story 14.2 (NFR-O11)."
        ),
    )
    parser.add_argument(
        "--dump-path",
        type=str,
        required=True,
        metavar="TXT",
        help=(
            "Path to a pre-captured `cosmic-ray dump` text file (one JSON pair "
            "per line), or '-' to read the dump from stdin."
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        metavar="N",
        help=(
            "If set, exit non-zero when score < N (percent). BUILT for Story 14.3; "
            "NOT wired into any gate in 14.2 — the nightly baseline runs without it."
        ),
    )
    args = parser.parse_args(argv)

    # Resolve the input source: stdin ('-') or a pre-captured dump file. This
    # script never spawns `cosmic-ray dump` itself — the caller pipes the dump
    # text in (see the `just mutation-*` recipes).
    if args.dump_path == "-":
        text = sys.stdin.read()
    else:
        dump_path = Path(args.dump_path)
        if not dump_path.exists():
            print(f"error: {dump_path} not found.", file=sys.stderr)
            return 1
        try:
            text = dump_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"error: failed to read {dump_path}: {exc}", file=sys.stderr)
            return 1

    try:
        results = parse_dump_text(text)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"error: failed to parse cosmic-ray dump: {exc}", file=sys.stderr)
        return 1

    score = compute_score(results)
    print(format_score_line(score))

    if args.threshold is not None:
        if score.checked == 0:
            print(
                f"error: --threshold {args.threshold:.1f}% requested but no mutants "
                "were checked (denominator is 0).",
                file=sys.stderr,
            )
            return 1
        if score.pct < args.threshold:
            print(
                f"FAIL: mutation score {score.pct:.1f}% < threshold {args.threshold:.1f}%",
                file=sys.stderr,
            )
            return 1
        print(f"OK: mutation score {score.pct:.1f}% >= threshold {args.threshold:.1f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
