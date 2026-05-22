#!/usr/bin/env python3
"""check_registry_isolation.py — enforce paired-restore for ``unregister_all()`` (Story 8.7.5 PP3).

CI gate that prevents recurrence of the Story 7e4ffec / Story 8.7.5 PP1
regression: a test that calls ``events.schema_registry.unregister_all()``
in teardown WITHOUT calling ``ensure_registered()`` (or an equivalent
snapshot/restore) leaks an empty registry into sibling tests, causing
``EventSchemaUnknown`` failures whenever pytest's collection order puts
a consumer test after the leaker.

Rule (RI001):
  Every ``unregister_all()`` call site (excluding the definition in
  ``packages/events/src/events/schema_registry.py``) MUST live in a
  function/fixture whose body ALSO contains at least one of:

    1. A call to ``ensure_registered(...)`` (canonical restore — recommended)
    2. A snapshot-then-replay pattern:
         - ``REGISTRY.clear()`` + ``REGISTRY.update(...)`` OR
         - a ``dict(REGISTRY)`` / ``REGISTRY.copy()`` assignment paired with
           ``register(...)`` calls after ``unregister_all()``
    3. Explicit re-registration: at least one ``register(...)`` call AFTER
       the ``unregister_all()`` in the same function.

  Suppression: ``# noqa: RI001 <reason>`` on the offending line.

Usage:
  uv run python scripts/check_registry_isolation.py
  uv run python scripts/check_registry_isolation.py --verbose
  uv run python scripts/check_registry_isolation.py --self-test
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

# Roots scanned for test files. Production source paths are excluded so
# the gate only reports against test code (the only legitimate caller of
# ``unregister_all`` per its docstring "Test-only helper").
_SCAN_ROOTS = [
    REPO_ROOT / "packages",
    REPO_ROOT / "services",
    REPO_ROOT / "tests",
    REPO_ROOT / "mcp-servers",
]

# Skip the registry's own source files (``unregister_all`` is defined here).
_EXCLUDED_FILES = {
    REPO_ROOT / "packages" / "events" / "src" / "events" / "schema_registry.py",
}

_EXTRA_SKIP = DEFAULT_SKIP_DIRS | {"fixtures"}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _is_unregister_all_call(node: ast.Call) -> bool:
    """True if *node* is a call to ``unregister_all(...)``.

    Matches both ``unregister_all()`` (bare name import) and
    ``sr.unregister_all()`` / ``schema_registry.unregister_all()`` attribute
    forms.
    """
    func = node.func
    if isinstance(func, ast.Name) and func.id == "unregister_all":
        return True
    return isinstance(func, ast.Attribute) and func.attr == "unregister_all"


def _is_ensure_registered_call(node: ast.Call) -> bool:
    """True if *node* is a call to ``ensure_registered(...)``."""
    func = node.func
    if isinstance(func, ast.Name) and func.id == "ensure_registered":
        return True
    return isinstance(func, ast.Attribute) and func.attr == "ensure_registered"


def _is_register_call(node: ast.Call) -> bool:
    """True if *node* is a call to ``register(...)`` or a restore-intent alias.

    Recognizes:
      - ``register(...)``, ``sr.register(...)``, ``schema_registry.register(...)``
      - ``_reg(...)`` — common alias in test_materializer.py
      - Any callable whose bare name contains ``register`` but NOT ``unregister``
        (matches ``_ensure_secret_accessed_registered``, ``re_register``, etc. —
        test-local wrappers that re-register canonical types after a wipe).
    """
    func = node.func
    name: str | None = None
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    if name is None:
        return False
    if name in {"register", "_reg"}:
        return True
    lower = name.lower()
    return "register" in lower and "unregister" not in lower


def _is_registry_snapshot_call(node: ast.Call) -> bool:
    """True if *node* is ``dict(REGISTRY)`` or ``REGISTRY.copy()`` — snapshot patterns."""
    func = node.func
    # dict(REGISTRY)
    if (
        isinstance(func, ast.Name)
        and func.id == "dict"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "REGISTRY"
    ):
        return True
    # REGISTRY.copy()
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "copy"
        and isinstance(func.value, ast.Name)
        and func.value.id == "REGISTRY"
    ):
        return True
    # REGISTRY.update(...)  — replay arm of snapshot/restore
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "update"
        and isinstance(func.value, ast.Name)
        and func.value.id == "REGISTRY"
    )


def _has_restore_pattern(func_body: list[ast.stmt]) -> bool:
    """True if *func_body* contains any registry-restore pattern.

    Walks every descendant of *func_body* (so a try/finally restore inside
    the function still counts).
    """
    for stmt in func_body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            if _is_ensure_registered_call(node):
                return True
            if _is_registry_snapshot_call(node):
                return True
            if _is_register_call(node):
                return True
    return False


def _enclosing_function(
    tree: ast.AST, target: ast.AST
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the innermost FunctionDef / AsyncFunctionDef enclosing *target*.

    Returns None if *target* is at module scope.
    """
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for parent in ast.walk(tree):
        if isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef):
            for child in ast.walk(parent):
                if child is target:
                    # Found target inside parent; this parent is a
                    # candidate. Continue walking to find the innermost.
                    best = parent
                    break
    return best


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def _scan(roots: list[Path]) -> tuple[list[Violation], int]:
    """Scan *roots*, return (violations, files_scanned)."""
    violations: list[Violation] = []
    scanned = 0

    for path in walk_python_files(roots, skip_dirs=_EXTRA_SKIP):
        if path in _EXCLUDED_FILES:
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

        # Find every unregister_all() call in this file.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not _is_unregister_all_call(node):
                continue

            lineno = node.lineno
            source_line = lines[lineno - 1] if lineno <= len(lines) else ""
            if has_noqa(source_line, "RI001"):
                continue

            # Find the enclosing function/fixture.
            enclosing = _enclosing_function(tree, node)
            if enclosing is None:
                # Module-level unregister_all() — rare; treat as violation
                # (callers should wrap in fixtures so teardown is paired).
                violations.append(
                    Violation(
                        file=path,
                        lineno=lineno,
                        rule="RI001",
                        message=(
                            "unregister_all() at module scope — wrap in a fixture "
                            "and pair with ensure_registered() to restore canonical "
                            "registry state for sibling tests."
                        ),
                    )
                )
                continue

            if _has_restore_pattern(enclosing.body):
                continue

            violations.append(
                Violation(
                    file=path,
                    lineno=lineno,
                    rule="RI001",
                    message=(
                        f"unregister_all() in {enclosing.name!r} has no paired "
                        "restore. Add ensure_registered() (preferred), a "
                        "snapshot/restore via dict(REGISTRY) + REGISTRY.update, "
                        "or explicit register() calls. See docs/testing-guide.md "
                        '"Schema-registry isolation in tests".'
                    ),
                )
            )

    return violations, scanned


# ---------------------------------------------------------------------------
# Self-test harness
# ---------------------------------------------------------------------------


def _self_test() -> int:
    fixture_root = SCRIPTS_DIR / "checks" / "fixtures" / "registry_isolation"
    failures: list[str] = []

    clean_dir = fixture_root / "clean"
    violation_dir = fixture_root / "violations"

    if not clean_dir.exists() or not violation_dir.exists():
        print(
            f"FAIL: fixture dirs missing under {fixture_root}",
            file=sys.stderr,
        )
        return 1

    # Clean fixtures — expect zero violations
    clean_files = [p for p in clean_dir.glob("*.py") if not p.name.startswith("_")]
    viols, _ = _scan(clean_files)
    for v in viols:
        failures.append(f"FAIL clean/{v.file.name}:{v.lineno}: unexpected violation: {v.message}")

    # Violation fixtures — expect at least one violation per file
    viol_files = [p for p in violation_dir.glob("*.py") if not p.name.startswith("_")]
    for fpath in sorted(viol_files):
        viols, _ = _scan([fpath])
        if not viols:
            failures.append(f"FAIL violations/{fpath.name}: expected a violation but got none")

    if failures:
        print("check_registry_isolation.py --self-test FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    total = len(clean_files) + len(viol_files)
    print(f"✓ check_registry_isolation.py self-test OK ({total} fixtures, 0 failures)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_registry_isolation.py",
        description=(
            "Enforce paired ensure_registered()/snapshot-restore for every "
            "unregister_all() call in test code (Story 8.7.5 PP3)."
        ),
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
            f"\nregistry-isolation: {len(violations)} violation(s) in {scanned} file(s) scanned.",
            file=sys.stderr,
        )
        return 1

    if args.verbose:
        print(f"✓ registry-isolation OK ({scanned} files scanned, 0 violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
