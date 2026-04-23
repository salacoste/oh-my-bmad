#!/usr/bin/env python3
"""check_imports.py — enforce the platform import-graph rules (NFR-M1).

CI gate: walks all .py files under packages/, services/, mcp-servers/ and
fails (exit 1) on any cross-boundary import:

  - service → service  (cross-service)
  - mcp-server → service
  - mcp-server → mcp-server  (cross-mcp-server)
  - package → service
  - package → mcp-server

Allowed:
  - anything → package
  - service → service (same service)
  - mcp-server → mcp-server (same mcp-server)

Suppression: # noqa: IMP001 <reason>  on the offending import line.

Usage:
  uv run python scripts/check_imports.py            # normal CI scan
  uv run python scripts/check_imports.py --verbose  # print success summary
  uv run python scripts/check_imports.py --self-test # fixture harness
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent

# Add scripts/ to sys.path so we can import scripts.checks._common
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from checks._common import (  # noqa: E402
    DEFAULT_SKIP_DIRS,
    Violation,
    has_noqa,
    walk_python_files,
)

# Category literals
CAT_PACKAGE = "package"
CAT_SERVICE = "service"
CAT_MCP = "mcp-server"

# Scan roots relative to REPO_ROOT
SCAN_ROOTS = [
    (CAT_PACKAGE, REPO_ROOT / "packages"),
    (CAT_SERVICE, REPO_ROOT / "services"),
    (CAT_MCP, REPO_ROOT / "mcp-servers"),
]

# Dirs to skip within those roots (on top of DEFAULT_SKIP_DIRS)
EXTRA_SKIP = {"tests", "scripts", "migrator", "fixtures"}


# ---------------------------------------------------------------------------
# Build MODULE_TO_OWNER mapping from pyproject.toml files
# ---------------------------------------------------------------------------


def _kebab_to_snake(name: str) -> str:
    return name.replace("-", "_")


def _build_module_map() -> dict[str, tuple[str, str]]:
    """Return {module_name: (category, component_name)} for every workspace member."""
    mapping: dict[str, tuple[str, str]] = {}
    for category, root in SCAN_ROOTS:
        if not root.exists():
            continue
        for pyproject in root.glob("*/pyproject.toml"):
            component_dir = pyproject.parent
            component_name = component_dir.name  # e.g. "registry-api"
            # Parse project.name from pyproject.toml
            try:
                text = pyproject.read_text()
            except OSError:
                continue
            pkg_name: str | None = None
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("name") and "=" in stripped:
                    # name = "registry-api"
                    val = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                    pkg_name = val
                    break
            if pkg_name is None:
                continue
            module_name = _kebab_to_snake(pkg_name)
            mapping[module_name] = (category, component_name)
    return mapping


MODULE_TO_OWNER: dict[str, tuple[str, str]] = _build_module_map()


# ---------------------------------------------------------------------------
# File → owner resolution
# ---------------------------------------------------------------------------


def _owner_of_file(path: Path) -> tuple[str, str] | None:
    """Return (category, component_name) for a .py file, or None if unknown."""
    for category, root in SCAN_ROOTS:
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        # First segment under the root is the component dir
        component_name = rel.parts[0]
        return (category, component_name)
    return None


# ---------------------------------------------------------------------------
# Import target resolution
# ---------------------------------------------------------------------------


def _top_level(module: str) -> str:
    """Return the top-level package name from a dotted module path."""
    return module.split(".")[0]


def _owner_of_import(top_name: str) -> tuple[str, str] | None:
    """Return (category, component_name) for an imported top-level name, or None."""
    return MODULE_TO_OWNER.get(top_name)


# ---------------------------------------------------------------------------
# Violation rule check
# ---------------------------------------------------------------------------


def _check_rule(
    src_cat: str,
    src_name: str,
    tgt_cat: str,
    tgt_name: str,
) -> str | None:
    """Return an error message if the import is forbidden, else None."""
    # service → different service
    if src_cat == CAT_SERVICE and tgt_cat == CAT_SERVICE and src_name != tgt_name:
        return (
            f"cross-service import: {src_name!r} imports from service {tgt_name!r}. "
            "Share via packages/ or event/HTTP contract."
        )
    # mcp-server → service
    if src_cat == CAT_MCP and tgt_cat == CAT_SERVICE:
        return (
            f"mcp-server {src_name!r} imports from service {tgt_name!r}. "
            "mcp-servers may only import from packages/."
        )
    # mcp-server → different mcp-server
    if src_cat == CAT_MCP and tgt_cat == CAT_MCP and src_name != tgt_name:
        return (
            f"cross-mcp-server import: {src_name!r} imports from mcp-server {tgt_name!r}. "
            "mcp-servers may only import from packages/."
        )
    # package → service
    if src_cat == CAT_PACKAGE and tgt_cat == CAT_SERVICE:
        return (
            f"package {src_name!r} imports from service {tgt_name!r}. "
            "packages/ must never depend on services/."
        )
    # package → mcp-server
    if src_cat == CAT_PACKAGE and tgt_cat == CAT_MCP:
        return (
            f"package {src_name!r} imports from mcp-server {tgt_name!r}. "
            "packages/ must never depend on mcp-servers/."
        )
    return None


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def _iter_imports(tree: ast.Module) -> Iterator[tuple[int, str]]:
    """Yield (lineno, top_level_module) for every ABSOLUTE import in *tree*.

    Relative imports (`from .foo import bar`, level > 0) stay within the same
    service/package by definition and are never cross-boundary — skipped.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, _top_level(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.lineno, _top_level(node.module)


def _scan_file(
    path: Path,
    lines: list[str],
    src_cat: str,
    src_name: str,
) -> list[Violation]:
    """Return violations found in *path* given its owner (src_cat, src_name)."""
    try:
        tree = ast.parse("".join(lines), filename=str(path))
    except SyntaxError:
        return []

    violations: list[Violation] = []
    for lineno, top_name in _iter_imports(tree):
        tgt_owner = _owner_of_import(top_name)
        if tgt_owner is None:
            continue  # stdlib or third-party — skip
        tgt_cat, tgt_name = tgt_owner
        msg = _check_rule(src_cat, src_name, tgt_cat, tgt_name)
        if msg is None:
            continue
        # Check suppression
        source_line = lines[lineno - 1] if lineno <= len(lines) else ""
        if has_noqa(source_line, "IMP001"):
            continue
        violations.append(Violation(file=path, lineno=lineno, rule="IMP001", message=msg))
    return violations


def _scan(
    roots: list[tuple[str, Path]],
    *,
    owner_overrides: dict[Path, tuple[str, str]] | None = None,
) -> tuple[list[Violation], int]:
    """Scan *roots* and return (violations, files_scanned).

    owner_overrides: mapping from absolute path → (category, name); used by
    --self-test to inject synthetic ownership for fixture files.
    """
    skip = DEFAULT_SKIP_DIRS | EXTRA_SKIP
    violations: list[Violation] = []
    scanned = 0

    # Build list of (category, root_path) pairs for walk
    walk_roots = [root for _, root in roots]

    for path in walk_python_files(walk_roots, skip_dirs=skip):
        if owner_overrides and path in owner_overrides:
            src_cat, src_name = owner_overrides[path]
        else:
            owner = _owner_of_file(path)
            if owner is None:
                continue
            src_cat, src_name = owner

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except OSError:
            continue

        scanned += 1
        violations.extend(_scan_file(path, lines, src_cat, src_name))

    return violations, scanned


# ---------------------------------------------------------------------------
# Self-test harness
# ---------------------------------------------------------------------------


def _load_meta(meta_path: Path) -> dict[str, dict]:  # type: ignore[type-arg]
    """Load a _meta.py fixture metadata file and return its META dict."""
    spec = importlib.util.spec_from_file_location("_meta", meta_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {meta_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.META  # type: ignore[attr-defined]


def _self_test() -> int:
    """Run the fixture harness and return exit code."""
    fixture_root = SCRIPTS_DIR / "checks" / "fixtures" / "imports"
    failures: list[str] = []

    for subdir_name in ("clean", "violations"):
        subdir = fixture_root / subdir_name
        meta_path = subdir / "_meta.py"
        if not meta_path.exists():
            failures.append(f"Missing meta file: {meta_path}")
            continue

        meta = _load_meta(meta_path)

        for filename, spec in meta.items():
            fpath = subdir / filename
            if not fpath.exists():
                failures.append(f"Fixture file missing: {fpath}")
                continue

            owner: tuple[str, str] = spec["owner"]
            expect_violation: bool = spec["expect_violation"]

            try:
                lines = fpath.read_text(encoding="utf-8").splitlines(keepends=True)
            except OSError as exc:
                failures.append(f"Cannot read {fpath}: {exc}")
                continue

            try:
                ast.parse("".join(lines), filename=str(fpath))
            except SyntaxError as exc:
                failures.append(f"Syntax error in {fpath}: {exc}")
                continue

            src_cat, src_name = owner
            viols = _scan_file(fpath, lines, src_cat, src_name)
            got_violation = len(viols) > 0

            if got_violation != expect_violation:
                direction = "violation" if expect_violation else "clean"
                result = "violation found" if got_violation else "no violation"
                failures.append(
                    f"FAIL {fpath.name}: expected {direction}, got {result}"
                    + (f"\n  {viols[0]}" if viols else "")
                )

    if failures:
        print("check_imports.py --self-test FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    print(
        f"✓ check_imports.py self-test OK "
        f"({sum(len(_load_meta(fixture_root / s / '_meta.py')) for s in ('clean', 'violations'))} "
        f"fixtures, 0 failures)"
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_imports.py",
        description="Enforce platform import-graph rules (NFR-M1).",
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

    violations, scanned = _scan(SCAN_ROOTS)

    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        print(
            f"\nimport-graph: {len(violations)} violation(s) in {scanned} file(s) scanned.",
            file=sys.stderr,
        )
        return 1

    if args.verbose:
        print(f"✓ import-graph OK ({scanned} files scanned, 0 violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
