#!/usr/bin/env python3
"""check_single_writer.py — enforce the FR26 single-writer constraint.

CI gate: walks all .py files OUTSIDE services/registry-state/ (the sole
authorised writer), tests/, scripts/migrator/, scripts/checks/fixtures/,
upstream/, .venv/, _bmad*/ and cache dirs.

AST-finds SQLAlchemy mutation patterns:
  - session.add(...)
  - session.add_all(...)
  - session.merge(...)
  - session.delete(...)
  - session.execute(insert(...))
  - session.execute(update(...))
  - session.execute(delete(...))
  - conn.execute(insert|update|delete(...))

Suppression: # noqa: SW001 <reason>  on the offending line.

Usage:
  uv run python scripts/check_single_writer.py
  uv run python scripts/check_single_writer.py --verbose
  uv run python scripts/check_single_writer.py --self-test
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

# SQLAlchemy session/connection mutation method names
_SESSION_WRITE_ATTRS = {"add", "add_all", "merge", "delete"}

# SQLAlchemy DML constructors (used inside session.execute(...))
_DML_CONSTRUCTORS = {"insert", "update", "delete"}

# Skip these dirs/names when walking for single-writer checks.
# DEFAULT_SKIP_DIRS already covers _bmad, _bmad-output, .venv, upstream, cache
# dirs, and AI-tool dotdirs. Only add entries specific to this check:
#   - tests: skip the test tree
#   - fixtures: skip the self-test fixture tree
#   - migrator: standalone operator script, not platform code (scripts/migrator/)
_SW_SKIP_DIRS = DEFAULT_SKIP_DIRS | {
    "tests",
    "fixtures",
    "migrator",
}

# Absolute paths excluded from scanning (registry-state is the sole writer).
# packages/idempotency is also excluded: it owns the standalone
# idempotency_cache table per FR28 and writes only to that table — never to
# tasks/sessions/events. The duplicated Core Table is what allows the package
# to remain dependency-free of services/.
_EXCLUDED_ROOTS = [
    REPO_ROOT / "services" / "registry-state",
    REPO_ROOT / "packages" / "idempotency",
]

# Roots to scan
_SCAN_ROOTS = [
    REPO_ROOT / "packages",
    REPO_ROOT / "services",
    REPO_ROOT / "mcp-servers",
    REPO_ROOT / "src",
]


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _receiver_name(node: ast.expr) -> str | None:
    """Return the base name of a receiver expression.

    Handles Name, Attribute (returning last .attr), and Call (returning the
    callable's name, so `get_session().add()` is recognized). Unknown forms
    return None.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        # e.g. get_session().add(x) — inspect the callable
        inner = node.func
        if isinstance(inner, ast.Name):
            return inner.id
        if isinstance(inner, ast.Attribute):
            return inner.attr
    return None


# Exact receiver names recognized as SQLAlchemy session/connection objects.
# Extending this set requires a # noqa: SW001 justification for each new name.
_SESSION_NAMES = frozenset({"session", "conn", "connection", "db", "sess"})


def _is_session_like(name: str | None) -> bool:
    """True if the receiver name is an exact SQLAlchemy session/conn identifier."""
    return name is not None and name.lower() in _SESSION_NAMES


def _walk_to_root_dml(node: ast.expr) -> str | None:
    """Walk the leftmost-call chain on *node* and return the root DML constructor name.

    Handles bare forms (`insert(Model)`) and chains (`insert(Model).values(x=1).returning(...)`)
    by unwrapping Attribute `.value` accesses until the root Call is found.
    Returns the DML constructor name if the root is `insert`/`update`/`delete`, else None.
    """
    current: ast.expr | None = node
    while isinstance(current, ast.Call):
        func = current.func
        if isinstance(func, ast.Name) and func.id in _DML_CONSTRUCTORS:
            return func.id
        if isinstance(func, ast.Attribute):
            # Method call on some receiver — recurse into the receiver
            current = func.value
            continue
        return None
    return None


def _is_write_call(node: ast.Call) -> tuple[bool, str]:
    """Return (is_violation, description) for a Call node."""
    func = node.func

    # Pattern 1: session.add(...) / session.add_all(...) / session.merge(...) / session.delete(...)
    if isinstance(func, ast.Attribute) and func.attr in _SESSION_WRITE_ATTRS:
        receiver = _receiver_name(func.value)
        if _is_session_like(receiver):
            return True, f"{receiver}.{func.attr}()"

    # Pattern 2: session.execute(insert|update|delete(...)) — including chains
    # like insert(Model).values(x=1).returning(Model.id), where the outermost
    # Call's func is an Attribute. Walk the leftmost-call chain back to find
    # the root DML constructor.
    if isinstance(func, ast.Attribute) and func.attr == "execute" and node.args:
        receiver = _receiver_name(func.value)
        if _is_session_like(receiver):
            first_arg = node.args[0]
            root_dml = _walk_to_root_dml(first_arg)
            if root_dml is not None:
                return True, f"{receiver}.execute({root_dml}(...))"

    return False, ""


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def _scan(roots: list[Path]) -> tuple[list[Violation], int]:
    """Scan *roots* and return (violations, files_scanned)."""
    violations: list[Violation] = []
    scanned = 0

    for path in walk_python_files(roots, skip_dirs=_SW_SKIP_DIRS):
        # Skip files that fall under any excluded root
        if any(path == excl or path.is_relative_to(excl) for excl in _EXCLUDED_ROOTS):
            continue

        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue

        lines = source.splitlines()
        scanned += 1

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            is_viol, desc = _is_write_call(node)
            if not is_viol:
                continue

            lineno = node.lineno
            source_line = lines[lineno - 1] if lineno <= len(lines) else ""
            if has_noqa(source_line, "SW001"):
                continue

            violations.append(
                Violation(
                    file=path,
                    lineno=lineno,
                    rule="SW001",
                    message=(
                        f"{desc} — SQLAlchemy write outside services/registry-state/. "
                        "Only registry-state may write to the database (FR26)."
                    ),
                )
            )

    return violations, scanned


# ---------------------------------------------------------------------------
# Self-test harness
# ---------------------------------------------------------------------------


def _self_test() -> int:
    fixture_root = SCRIPTS_DIR / "checks" / "fixtures" / "single_writer"
    failures: list[str] = []

    # Clean fixtures — expect zero violations
    clean_dir = fixture_root / "clean"
    viols, _ = _scan([clean_dir])
    for v in viols:
        failures.append(f"FAIL clean/{v.file.name}:{v.lineno}: unexpected violation: {v.message}")

    # Violation fixtures — expect at least one violation per file
    violation_dir = fixture_root / "violations"
    for fpath in sorted(violation_dir.glob("*.py")):
        if fpath.name.startswith("_"):
            continue
        viols, _ = _scan([fpath])
        if not viols:
            failures.append(f"FAIL violations/{fpath.name}: expected a violation but got none")

    if failures:
        print("check_single_writer.py --self-test FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    clean_count = len(list(clean_dir.glob("*.py")))
    viol_count = len(list(violation_dir.glob("*.py")))
    print(
        f"✓ check_single_writer.py self-test OK ({clean_count + viol_count} fixtures, 0 failures)"
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_single_writer.py",
        description="Enforce the FR26 single-writer constraint.",
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

    violations, scanned = _scan(_SCAN_ROOTS)

    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        print(
            f"\nsingle-writer: {len(violations)} violation(s) in {scanned} file(s) scanned.",
            file=sys.stderr,
        )
        return 1

    if args.verbose:
        print(f"✓ single-writer OK ({scanned} files scanned, 0 violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
