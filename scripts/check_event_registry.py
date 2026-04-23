#!/usr/bin/env python3
"""check_event_registry.py — enforce event-type registration (NFR-O1 / FR18b).

CI gate: walks all .py files under services/ and mcp-servers/, AST-finds
emission call sites, and verifies every string-literal `type=` argument
is present in packages/events/src/events/schema_registry.REGISTRY.

Matched call patterns:
  - EventEnvelope(..., type="foo.bar", ...)
  - emit_event(..., type="foo.bar", ...)
  - <anything>.emit(..., type="foo.bar", ...)   (catches clawhip.emit(...))

Rules:
  - String literal not in REGISTRY  → error  (exit 1)
  - Non-literal type= without noqa  → warning (exit 1)
  - # noqa: EVT001 <reason>          → suppresses both error and warning

Usage:
  uv run python scripts/check_event_registry.py
  uv run python scripts/check_event_registry.py --verbose
  uv run python scripts/check_event_registry.py --self-test
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

SCAN_ROOTS = [
    REPO_ROOT / "services",
    REPO_ROOT / "mcp-servers",
]

EXTRA_SKIP = {"tests", "fixtures"}

# Callable names / attribute names that trigger inspection
_EMIT_NAMES = {"EventEnvelope", "emit_event"}  # Name(id=...)
_EMIT_ATTRS = {"emit"}  # Attribute(attr=...)


# ---------------------------------------------------------------------------
# Import REGISTRY (with graceful failure)
# ---------------------------------------------------------------------------


def _load_registry() -> frozenset[str]:
    try:
        from events.schema_registry import REGISTRY  # type: ignore[import-untyped]

        return REGISTRY
    except ImportError:
        print(
            "check_event_registry.py: cannot import events.schema_registry — run `uv sync` first",
            file=sys.stderr,
        )
        sys.exit(2)


# ---------------------------------------------------------------------------
# Core scan logic (private, accepts registry so --self-test can inject its own)
# ---------------------------------------------------------------------------


def _is_emit_call(node: ast.Call) -> bool:
    """Return True if *node* is one of the tracked emission call patterns."""
    func = node.func
    return (isinstance(func, ast.Name) and func.id in _EMIT_NAMES) or (
        isinstance(func, ast.Attribute) and func.attr in _EMIT_ATTRS
    )


def _scan(
    roots: list[Path],
    registry: frozenset[str],
) -> tuple[list[Violation], int]:
    """Scan *roots* against *registry*; return (violations, files_scanned)."""
    skip = DEFAULT_SKIP_DIRS | EXTRA_SKIP
    violations: list[Violation] = []
    scanned = 0

    for path in walk_python_files(roots, skip_dirs=skip):
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
            if not _is_emit_call(node):
                continue

            # Find the `type=` keyword argument
            type_kw: ast.keyword | None = None
            for kw in node.keywords:
                if kw.arg == "type":
                    type_kw = kw
                    break
            if type_kw is None:
                continue

            lineno = type_kw.value.lineno
            source_line = lines[lineno - 1] if lineno <= len(lines) else ""

            # Suppressed?
            if has_noqa(source_line, "EVT001"):
                continue

            val = type_kw.value
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                # String literal — check registry
                if val.value not in registry:
                    violations.append(
                        Violation(
                            file=path,
                            lineno=lineno,
                            rule="EVT001",
                            message=(
                                f"event type {val.value!r} is not registered in "
                                "events.schema_registry.REGISTRY. "
                                "Add it there before emitting."
                            ),
                        )
                    )
            else:
                # Non-literal — warn; require noqa
                violations.append(
                    Violation(
                        file=path,
                        lineno=lineno,
                        rule="EVT001",
                        message=(
                            "non-literal type= argument in emission call — "
                            "cannot verify at scan time. "
                            "Add # noqa: EVT001 <reason> to suppress."
                        ),
                    )
                )

    return violations, scanned


# ---------------------------------------------------------------------------
# Self-test harness
# ---------------------------------------------------------------------------


def _self_test() -> int:
    fixture_root = SCRIPTS_DIR / "checks" / "fixtures" / "events"
    registry_path = fixture_root / "clean" / "registry.py"

    # Load fixture-local registry
    if not registry_path.exists():
        print(f"FAIL: fixture registry missing: {registry_path}", file=sys.stderr)
        return 1

    ns: dict[str, object] = {}
    exec(registry_path.read_text(), ns)  # noqa: S102
    fixture_registry: frozenset[str] = ns.get("REGISTRY", frozenset())  # type: ignore[assignment]

    failures: list[str] = []

    # --- clean fixtures ---
    clean_dir = fixture_root / "clean"
    clean_files = [p for p in clean_dir.glob("*.py") if p.name not in {"registry.py", "_meta.py"}]
    viols, _ = _scan(clean_files, fixture_registry)
    if viols:
        for v in viols:
            failures.append(
                f"FAIL clean/{v.file.name}:{v.lineno}: unexpected violation: {v.message}"
            )

    # --- violation fixtures ---
    violation_dir = fixture_root / "violations"
    for fpath in sorted(violation_dir.glob("*.py")):
        if fpath.name.startswith("_"):
            continue
        viols, _ = _scan([fpath], fixture_registry)
        if not viols:
            failures.append(f"FAIL violations/{fpath.name}: expected a violation but got none")

    if failures:
        print("check_event_registry.py --self-test FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    total = len(clean_files) + len(list(violation_dir.glob("*.py")))
    print(f"✓ check_event_registry.py self-test OK ({total} fixtures, 0 failures)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_event_registry.py",
        description="Enforce event-type registration (NFR-O1 / FR18b).",
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

    registry = _load_registry()
    violations, scanned = _scan(SCAN_ROOTS, registry)

    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        print(
            f"\nevent-registry: {len(violations)} violation(s) in {scanned} file(s) scanned.",
            file=sys.stderr,
        )
        return 1

    if args.verbose:
        print(
            f"✓ event-registry OK "
            f"({scanned} files scanned, {len(registry)} registered types, 0 violations)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
