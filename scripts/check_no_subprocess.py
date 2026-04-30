#!/usr/bin/env python3
"""check_no_subprocess.py — enforce NFR-S5 "no shell on the request path".

CI gate (Story 3.8): walks the spine + bot source trees and rejects any
``import subprocess`` / ``from subprocess import …`` / ``os.system`` /
``os.popen`` / ``os.exec*`` / ``subprocess.<anything>`` attribute access.

Suppression: ``# noqa: SHELL001 <reason>`` on the offending line. The
reason MUST be non-empty (matches ``checks._common._NOQA_RE``); a bare
``# noqa: SHELL001`` is rejected. Story 5.4 will use this exemption to
opt-in the Claude Code CLI supervision call site.

Spine-only walk (request-path scope):
  - INCLUDED:
      services/{telegram-gateway,registry-api,registry-state,clawhip-daemon}/src/
      mcp-servers/clawhip-bridge/src/
      packages/{events,idempotency,secret-hygiene}/src/
  - EXCLUDED:
      services/worker-wrapper/  (Story 5.4 territory; will add # noqa: SHELL001
                                 when the legitimate Claude Code CLI subprocess
                                 lands)
      tests/                    (fuzz harness monkeypatches subprocess by design)
      scripts/migrator/         (operator tool, not on request path)
      scripts/sync_upstream.py  (operator tool, not on request path)
      upstream/                 (vendored)
      anything outside the included list

Usage::

    uv run python scripts/check_no_subprocess.py             # CI scan
    uv run python scripts/check_no_subprocess.py --verbose   # success summary
    uv run python scripts/check_no_subprocess.py --self-test # fixture harness
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
# Detection sets
# ---------------------------------------------------------------------------

# Shell-escape entry points on the ``os`` module. ``exec*`` is the family
# ``execv`` / ``execvp`` / ``execvpe`` / ``execlp`` / ``execle`` / ``execve`` /
# ``execl`` — listed explicitly to avoid a startswith() that would also catch
# ``os.execute_query`` or similar look-alikes from third-party shadows.
_OS_SHELL_ATTRS: frozenset[str] = frozenset(
    {
        "system",
        "popen",
        "exec",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "execl",
        "execle",
        "execlp",
        "execlpe",
    }
)

# ---------------------------------------------------------------------------
# Spine-only scan roots (request-path contract)
# ---------------------------------------------------------------------------

_SPINE_ROOTS: list[Path] = [
    REPO_ROOT / "services" / "telegram-gateway" / "src",
    REPO_ROOT / "services" / "registry-api" / "src",
    REPO_ROOT / "services" / "registry-state" / "src",
    REPO_ROOT / "services" / "clawhip-daemon" / "src",
    REPO_ROOT / "mcp-servers" / "clawhip-bridge" / "src",
    REPO_ROOT / "packages" / "events" / "src",
    REPO_ROOT / "packages" / "idempotency" / "src",
    REPO_ROOT / "packages" / "secret-hygiene" / "src",
]

# Per-walk skip set — DEFAULT_SKIP_DIRS already covers __pycache__ + caches +
# vendored trees + AI-tool dotdirs. We additionally skip ``tests`` so co-located
# test files inside spine src/ trees are exempt from the SHELL001 rule (the
# fuzz harness itself imports ``subprocess`` to monkeypatch it).
_SPINE_SKIP: frozenset[str] = DEFAULT_SKIP_DIRS | frozenset({"tests", "fixtures"})


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------


class _ShellVisitor(ast.NodeVisitor):
    """Collect ``(lineno, message)`` tuples for every SHELL001 candidate."""

    def __init__(self) -> None:
        self.findings: list[tuple[int, str]] = []

    # `import subprocess` / `import subprocess as sp`
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "subprocess" or alias.name.startswith("subprocess."):
                self.findings.append(
                    (
                        node.lineno,
                        f"import {alias.name!r} — "
                        "subprocess is forbidden on the request path (NFR-S5)",
                    )
                )
        self.generic_visit(node)

    # `from subprocess import run, Popen, ...`
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "subprocess" and node.level == 0:
            names = ", ".join(alias.name for alias in node.names) or "*"
            self.findings.append(
                (
                    node.lineno,
                    f"from subprocess import {names} — "
                    "subprocess is forbidden on the request path (NFR-S5)",
                )
            )
        self.generic_visit(node)

    # `subprocess.run(...)` / `os.system(...)` / `os.popen(...)` / `os.exec*(...)`
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name):
            if node.value.id == "subprocess":
                self.findings.append(
                    (
                        node.lineno,
                        f"subprocess.{node.attr}(...) — "
                        "subprocess is forbidden on the request path (NFR-S5)",
                    )
                )
            elif node.value.id == "os" and node.attr in _OS_SHELL_ATTRS:
                self.findings.append(
                    (
                        node.lineno,
                        f"os.{node.attr}(...) — "
                        "shell entry point forbidden on the request path (NFR-S5)",
                    )
                )
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def _scan_file(path: Path) -> list[Violation]:
    """Return SHELL001 violations found in *path*.

    Per-line ``# noqa: SHELL001 <reason>`` suppresses a single finding;
    duplicate findings on the same physical line collapse into one entry to
    avoid spamming the operator with N copies of the same Attribute hit.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    lines = source.splitlines()
    visitor = _ShellVisitor()
    visitor.visit(tree)

    # Deduplicate by lineno — multiple AST hits on a chained attribute
    # (e.g., ``subprocess.Popen(...).communicate()``) should produce ONE
    # violation, not many. First message wins.
    seen: set[int] = set()
    violations: list[Violation] = []
    for lineno, message in visitor.findings:
        if lineno in seen:
            continue
        source_line = lines[lineno - 1] if lineno <= len(lines) else ""
        if has_noqa(source_line, "SHELL001"):
            continue
        seen.add(lineno)
        violations.append(Violation(file=path, lineno=lineno, rule="SHELL001", message=message))
    return violations


def _is_test_file(path: Path) -> bool:
    """True for co-located ``test_*.py`` / ``conftest.py`` files inside spine src/.

    Co-location is a project convention (architecture.md): unit tests live next
    to the source they cover. Those files legitimately import ``subprocess``
    (e.g. monkeypatching it under test) and must be exempt from the SHELL001
    rule, mirroring the broader ``tests/`` exclusion.
    """
    name = path.name
    return name.startswith("test_") or name == "conftest.py"


def _scan(roots: list[Path]) -> tuple[list[Violation], int]:
    """Scan *roots* and return ``(violations, files_scanned)``."""
    violations: list[Violation] = []
    scanned = 0
    for path in walk_python_files(roots, skip_dirs=_SPINE_SKIP):
        if _is_test_file(path):
            continue
        scanned += 1
        violations.extend(_scan_file(path))
    return violations, scanned


# ---------------------------------------------------------------------------
# Self-test harness
# ---------------------------------------------------------------------------


def _self_test() -> int:
    """Exercise bundled fixtures and assert SHELL001 detection works."""
    fixture_root = SCRIPTS_DIR / "checks" / "fixtures" / "no_subprocess"
    failures: list[str] = []

    # Clean fixtures — every file is suppressed via # noqa: SHELL001 <reason>.
    clean_dir = fixture_root / "clean"
    if not clean_dir.exists():
        failures.append(f"Missing clean fixtures directory: {clean_dir}")
    else:
        viols, _ = _scan([clean_dir])
        for v in viols:
            failures.append(
                f"FAIL clean/{v.file.name}:{v.lineno}: unexpected SHELL001: {v.message}"
            )

    # Violation fixtures — every file MUST surface at least one SHELL001.
    violation_dir = fixture_root / "violations"
    if not violation_dir.exists():
        failures.append(f"Missing violations fixtures directory: {violation_dir}")
    else:
        for fpath in sorted(violation_dir.glob("*.py")):
            if fpath.name.startswith("_"):
                continue
            viols = _scan_file(fpath)
            if not viols:
                failures.append(f"FAIL violations/{fpath.name}: expected SHELL001 but got none")

    if failures:
        print("check_no_subprocess.py --self-test FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    clean_count = len(list(clean_dir.glob("*.py"))) if clean_dir.exists() else 0
    viol_count = len(list(violation_dir.glob("*.py"))) if violation_dir.exists() else 0
    print(
        f"✓ check_no_subprocess.py self-test OK ({clean_count + viol_count} fixtures, 0 failures)"
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_no_subprocess.py",
        description="Enforce NFR-S5 'no shell on the request path' (Story 3.8).",
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

    violations, scanned = _scan(_SPINE_ROOTS)

    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        print(
            f"\nno-subprocess: {len(violations)} violation(s) in {scanned} file(s) scanned.",
            file=sys.stderr,
        )
        return 1

    if args.verbose:
        print(f"✓ no-subprocess OK ({scanned} files scanned, 0 violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
