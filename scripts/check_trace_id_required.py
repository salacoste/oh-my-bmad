#!/usr/bin/env python3
"""check_trace_id_required.py — enforce NFR-O7 "every emitted event carries trace_id".

CI gate: AST-scans the first-party source trees and rejects any
``EventEnvelope.create(...)`` call that does NOT pass a ``trace_id=`` keyword
argument. NFR-O7 requires every emitted event to carry a ``trace_id`` so the
end-to-end causal chain is reconstructable from the event log; since Phase-2
(FR57, schema 1.1.0 / Story 9.7) ``trace_id`` is a REQUIRED kwarg on the
factory (no default, no fallback) and a missing one raises
``pydantic.ValidationError`` at runtime. This guard moves that failure
left-of-runtime: a missing ``trace_id=`` is caught statically at CI time.

Phase-2 carryover (Story 9.7 deferral): the Phase-3 ship-blocker references
this gate but it was never built. This story closes that debt.

Pure regression-prevention: every real call site already passes ``trace_id=``,
so this guard MUST pass clean on the current tree and only fires if a future
commit adds an ``EventEnvelope.create(...)`` call that omits ``trace_id``.

What it FORBIDS (AST-scan, not naive grep, so docstrings/comments/strings that
merely mention ``EventEnvelope.create()`` never false-positive):

  * Any ``EventEnvelope.create(...)`` **attribute call** whose ``trace_id``
    keyword is absent. Both forms are detected:
      - ``EventEnvelope.create(...)``            (Name.create attribute call)
      - ``pkg.EventEnvelope.create(...)``        (Attr.create attribute call)
    A ``**kwargs`` splat is treated as POSSIBLY supplying ``trace_id`` (the
    keyword cannot be statically resolved), so such a call is NOT flagged —
    fail-open on the splat to avoid false positives (mirrors the precision
    bias of the sibling gates).

What it does NOT flag:
  * The ``EventEnvelope.create`` **definition** itself (``def create(...)``).
  * Calls to an unrelated ``.create(...)`` on some other object.
  * Calls inside excluded trees (tests, upstream/, fixtures, _bmad, …).

Suppression: ``# noqa: TRACE001 <reason>`` on the offending line. The reason
MUST be non-empty (matches ``checks._common._NOQA_RE``); a bare
``# noqa: TRACE001`` is rejected. Multiple tags on the same line are supported.

Scan roots (first-party source):
  - INCLUDED (glob-discovered so new components are covered automatically):
      services/*/src/
      mcp-servers/*/src/
      packages/*/src/
  - EXCLUDED:
      tests/ (test_*.py / conftest.py), */tests/* subtrees
      upstream/ (vendored), .venv, __pycache__ and friends
      scripts/checks/fixtures/ (the self-test fixtures intentionally violate)

Usage::

    uv run python scripts/check_trace_id_required.py             # CI scan
    uv run python scripts/check_trace_id_required.py --verbose   # success summary
    uv run python scripts/check_trace_id_required.py --self-test # fixture harness
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from checks._common import (  # noqa: E402
    DEFAULT_SKIP_DIRS,
    Violation,
    has_noqa,
    walk_python_files,
)

# ---------------------------------------------------------------------------
# Detection constants
# ---------------------------------------------------------------------------

# The factory whose every call must carry a ``trace_id`` keyword (NFR-O7).
_FACTORY_OWNER = "EventEnvelope"
_FACTORY_METHOD = "create"
_REQUIRED_KWARG = "trace_id"
_RULE = "TRACE001"


# ---------------------------------------------------------------------------
# First-party scan roots (NFR-O7)
# ---------------------------------------------------------------------------


def _discover_source_roots() -> list[Path]:
    """Glob-discover every workspace ``src/`` tree under the first-party dirs.

    Mirrors check_mcp_transport.py's discovery: rather than maintain a
    hand-curated list (which would silently exempt a newly added service or
    mcp-server), iterate the workspace layout. Widening the gate is safe today
    because every existing call site already passes ``trace_id=``.
    """
    roots: list[Path] = []
    for parent_name in ("services", "mcp-servers", "packages"):
        parent = REPO_ROOT / parent_name
        if not parent.exists():
            continue
        for src_dir in sorted(parent.glob("*/src")):
            if src_dir.is_dir():
                roots.append(src_dir)
    return roots


_SOURCE_ROOTS: list[Path] = _discover_source_roots()

# Per-walk skip set — DEFAULT_SKIP_DIRS already covers __pycache__ + caches +
# vendored trees (upstream/) + AI-tool dotdirs. We additionally skip ``tests``
# (co-located test subtrees) and ``fixtures`` (in-tree test data).
_SOURCE_SKIP: frozenset[str] = DEFAULT_SKIP_DIRS | frozenset({"tests", "fixtures"})


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------


def _is_factory_call(node: ast.Call) -> bool:
    """True iff *node* is an attribute call ``…EventEnvelope.create(...)``.

    Matches the attribute form precisely: the called func is an
    ``ast.Attribute`` whose ``attr`` is ``create`` and whose ``value`` resolves
    to a name/attribute ending in ``EventEnvelope`` (so both
    ``EventEnvelope.create`` and ``pkg.EventEnvelope.create`` match). A bare
    ``create(...)`` (no ``EventEnvelope`` qualifier) is intentionally NOT
    matched — it is too ambiguous and would risk false positives on unrelated
    ``.create`` factories.
    """
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != _FACTORY_METHOD:
        return False
    owner = func.value
    if isinstance(owner, ast.Name):
        return owner.id == _FACTORY_OWNER
    if isinstance(owner, ast.Attribute):
        return owner.attr == _FACTORY_OWNER
    return False


def _call_has_required_kwarg(node: ast.Call) -> bool:
    """True iff *node* passes ``trace_id=`` (or a ``**kwargs`` splat that might).

    A ``**kwargs`` splat (``ast.keyword`` with ``arg is None``) cannot be
    statically resolved, so we treat it as POSSIBLY supplying ``trace_id`` and
    do not flag — fail-open on the splat to avoid false positives.
    """
    for kw in node.keywords:
        if kw.arg == _REQUIRED_KWARG:
            return True
        if kw.arg is None:  # **kwargs splat — could carry trace_id
            return True
    return False


class _TraceIdVisitor(ast.NodeVisitor):
    """Collect ``(lineno, message)`` for every ``EventEnvelope.create`` call
    missing a ``trace_id=`` keyword (NFR-O7 / TRACE001)."""

    def __init__(self) -> None:
        self.findings: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        if _is_factory_call(node) and not _call_has_required_kwarg(node):
            self.findings.append(
                (
                    node.lineno,
                    f"{_FACTORY_OWNER}.{_FACTORY_METHOD}(...) call missing required "
                    f"'{_REQUIRED_KWARG}=' keyword — every emitted event must carry "
                    "a trace_id (NFR-O7); mint one via "
                    "events.envelope.is_valid_trace_id-validated input or a fresh "
                    "UUIDv7 at the entry boundary",
                )
            )
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def _scan_file(path: Path, *, raise_on_syntax: bool = False) -> list[Violation]:
    """Return TRACE001 violations found in *path*.

    Per-line ``# noqa: TRACE001 <reason>`` suppresses a single finding;
    duplicate findings on the same physical line collapse into one entry.
    ``SyntaxError`` surfaces as an empty result by default (mypy --strict would
    fail first); set ``raise_on_syntax=True`` to fail loudly (self-test).
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        if raise_on_syntax:
            raise
        return []

    lines = source.splitlines()
    visitor = _TraceIdVisitor()
    visitor.visit(tree)

    seen: set[int] = set()
    violations: list[Violation] = []
    for lineno, message in visitor.findings:
        if lineno in seen:
            continue
        source_line = lines[lineno - 1] if lineno <= len(lines) else ""
        if has_noqa(source_line, _RULE):
            continue
        seen.add(lineno)
        violations.append(Violation(file=path, lineno=lineno, rule=_RULE, message=message))
    return violations


def _file_contains_factory_violation(path: Path) -> bool:
    """Return True iff *path* contains ≥1 missing-trace_id factory call.

    Used by ``--self-test`` to assert ``clean/`` fixtures actually exercise the
    suppression path (the file DOES contain a missing-trace_id call, but it is
    silenced via ``# noqa: TRACE001 <reason>``). Walks the AST IGNORING noqa.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return False
    visitor = _TraceIdVisitor()
    visitor.visit(tree)
    return bool(visitor.findings)


def _is_test_file(path: Path) -> bool:
    """True for co-located ``test_*.py`` / ``conftest.py`` files inside src/ trees.

    Test files may legitimately construct envelopes without trace_id (e.g. to
    assert the ValidationError fires) and are exempt, mirroring the broader
    ``tests/`` exclusion.
    """
    name = path.name
    return name.startswith("test_") or name == "conftest.py"


def _scan(roots: list[Path]) -> tuple[list[Violation], int]:
    """Scan *roots* and return ``(violations, files_scanned)``."""
    violations: list[Violation] = []
    scanned = 0
    for path in walk_python_files(roots, skip_dirs=_SOURCE_SKIP):
        if _is_test_file(path):
            continue
        scanned += 1
        violations.extend(_scan_file(path))
    return violations, scanned


# ---------------------------------------------------------------------------
# Self-test harness
# ---------------------------------------------------------------------------


def _self_test() -> int:
    """Exercise bundled fixtures and assert TRACE001 detection works.

    ``clean/`` fixtures must contain ≥1 missing-trace_id factory call (so the
    suppression path is exercised) yet yield zero un-suppressed violations.
    ``violations/`` fixtures must each surface ≥1 TRACE001.
    """
    fixture_root = SCRIPTS_DIR / "checks" / "fixtures" / "trace_id_required"
    failures: list[str] = []

    def _list_fixture_files(directory: Path) -> list[Path]:
        return sorted(
            p
            for p in walk_python_files([directory], skip_dirs=DEFAULT_SKIP_DIRS)
            if not p.name.startswith("_")
        )

    # Clean fixtures — every missing-trace_id call is suppressed via # noqa: TRACE001.
    clean_dir = fixture_root / "clean"
    clean_files: list[Path] = []
    if not clean_dir.exists():
        failures.append(f"Missing clean fixtures directory: {clean_dir}")
    else:
        clean_files = _list_fixture_files(clean_dir)
        if not clean_files:
            failures.append(f"Empty clean fixtures directory: {clean_dir}")
        for fpath in clean_files:
            if not _file_contains_factory_violation(fpath):
                failures.append(
                    f"FAIL clean/{fpath.name}: contains no missing-trace_id "
                    "factory call — suppression path not exercised"
                )
            viols = _scan_file(fpath)
            for v in viols:
                failures.append(
                    f"FAIL clean/{v.file.name}:{v.lineno}: unexpected TRACE001: {v.message}"
                )

    # Violation fixtures — every file MUST surface at least one TRACE001.
    violation_dir = fixture_root / "violations"
    violation_files: list[Path] = []
    if not violation_dir.exists():
        failures.append(f"Missing violations fixtures directory: {violation_dir}")
    else:
        violation_files = _list_fixture_files(violation_dir)
        if not violation_files:
            failures.append(f"Empty violations fixtures directory: {violation_dir}")
        for fpath in violation_files:
            viols = _scan_file(fpath)
            if not viols:
                failures.append(f"FAIL violations/{fpath.name}: expected TRACE001 but got none")

    if failures:
        print("check_trace_id_required.py --self-test FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    total = len(clean_files) + len(violation_files)
    print(f"✓ check_trace_id_required.py self-test OK ({total} fixtures, 0 failures)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_trace_id_required.py",
        description="Enforce NFR-O7 'every emitted event carries trace_id'.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run against bundled fixtures and assert expected outcomes.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print success summary even when there are no violations.",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    violations, scanned = _scan(_SOURCE_ROOTS)

    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        print(
            f"\ntrace-id-required: {len(violations)} violation(s) in {scanned} file(s) scanned.",
            file=sys.stderr,
        )
        return 1

    if args.verbose:
        print(f"✓ trace-id-required OK ({scanned} files scanned, 0 violations)")
        for root in _SOURCE_ROOTS:
            count = sum(1 for _ in walk_python_files([root], skip_dirs=_SOURCE_SKIP))
            try:
                rel = root.relative_to(REPO_ROOT)
            except ValueError:
                rel = root
            print(f"    {rel}: {count} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
