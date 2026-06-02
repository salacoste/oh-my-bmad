#!/usr/bin/env python3
"""check_mcp_transport.py — enforce Phase-2 invariant P2-I4 "MCP transport stdio-only".

CI gate: walks the first-party source trees and rejects any ``import`` /
``from-import`` of the forbidden non-stdio MCP transport modules/names. The
MCP servers in this repo speak the MCP protocol over **stdio only**; the SSE
and streamable-HTTP transports must never be introduced because they would
open a network ingress surface (P2-I4 ship-blocker; also violates P2-I5
"no unexpected network ingress").

Pure regression-prevention: the codebase currently uses stdio exclusively
(zero forbidden imports), so this guard MUST pass clean on the current tree
and only fires if a future commit reaches for a non-stdio transport.

What it FORBIDS (AST-scan, not naive grep, so comments/strings never
false-positive):

  * Forbidden transport submodules of ``mcp.server``:
      - ``mcp.server.sse``
      - ``mcp.server.streamable_http``  (and any ``mcp.server.streamable*``)
    Detected in every import form:
      - ``import mcp.server.sse``
      - ``import mcp.server.sse as transport``
      - ``from mcp.server.sse import SseServerTransport``
      - ``from mcp.server import sse``           (submodule via parent package)
      - ``from mcp.server import streamable_http``
  * Forbidden transport *names* (used to mount non-stdio transports),
    regardless of where they are imported from:
      - ``SseServerTransport``
      - ``sse_app``
      - ``streamable_http_app``

What it ALLOWS (stdio — the sanctioned transport):
  * ``from mcp.server.stdio import stdio_server``
  * ``import mcp.server`` / ``from mcp.server import Server`` / ``from mcp import …``
  * any other ``mcp.*`` import that is not an SSE / streamable-HTTP submodule
    and does not pull in a forbidden transport name.

Suppression: ``# noqa: MCP001 <reason>`` on the offending line. The reason
MUST be non-empty (matches ``checks._common._NOQA_RE``); a bare
``# noqa: MCP001`` is rejected. Multiple tags on the same line are supported
(mirroring SHELL001/IMP001): ``# noqa: PLC0415, MCP001 — reason``.

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

    uv run python scripts/check_mcp_transport.py             # CI scan
    uv run python scripts/check_mcp_transport.py --verbose   # success summary
    uv run python scripts/check_mcp_transport.py --self-test # fixture harness
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

# Forbidden transport submodules of the ``mcp.server`` package. Matched
# exactly for ``mcp.server.sse`` and via a ``streamable`` prefix for the
# streamable-HTTP family (``mcp.server.streamable_http`` and any future
# ``mcp.server.streamable*`` rename). The legitimate stdio transport lives
# at ``mcp.server.stdio`` and is deliberately NOT in this set.
_FORBIDDEN_SERVER_SUBMODULES: frozenset[str] = frozenset({"sse"})
_FORBIDDEN_SERVER_SUBMODULE_PREFIXES: tuple[str, ...] = ("streamable",)

# Forbidden transport names — commonly imported to mount a non-stdio
# transport. Flagged no matter which module they are imported from, so a
# re-export shim cannot smuggle them past the submodule check.
_FORBIDDEN_TRANSPORT_NAMES: frozenset[str] = frozenset(
    {
        "SseServerTransport",
        "sse_app",
        "streamable_http_app",
    }
)


def _is_forbidden_server_submodule(submodule: str) -> bool:
    """True iff *submodule* (the part after ``mcp.server.``) is a forbidden transport."""
    if submodule in _FORBIDDEN_SERVER_SUBMODULES:
        return True
    return any(submodule.startswith(prefix) for prefix in _FORBIDDEN_SERVER_SUBMODULE_PREFIXES)


def _is_forbidden_module_path(module: str) -> bool:
    """True iff a dotted *module* path is (or descends into) a forbidden transport.

    Matches ``mcp.server.sse`` / ``mcp.server.sse.foo`` /
    ``mcp.server.streamable_http`` / ``mcp.server.streamable_http.bar``. Does
    NOT match ``mcp.server.stdio`` or any non-``mcp.server`` module.
    """
    parts = module.split(".")
    if len(parts) >= 3 and parts[0] == "mcp" and parts[1] == "server":
        return _is_forbidden_server_submodule(parts[2])
    return False


# ---------------------------------------------------------------------------
# First-party scan roots (P2-I4 stdio-only contract)
# ---------------------------------------------------------------------------


def _discover_source_roots() -> list[Path]:
    """Glob-discover every workspace ``src/`` tree under the first-party dirs.

    Mirrors check_no_subprocess.py's spine discovery: rather than maintain a
    hand-curated list (which would silently exempt a newly added service or
    mcp-server), iterate the workspace layout. Widening the gate is safe today
    because ZERO forbidden transports are imported anywhere.
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


class _TransportVisitor(ast.NodeVisitor):
    """Collect ``(lineno, message)`` tuples for every MCP001 candidate.

    Detection covers:
      * ``import mcp.server.sse[.…]`` / ``import mcp.server.streamable_http``
        (with or without ``as <alias>``) — via the forbidden module-path check.
      * ``from mcp.server.sse import …`` / ``from mcp.server.streamable_http
        import …`` — forbidden module being imported FROM.
      * ``from mcp.server import sse`` / ``from mcp.server import
        streamable_http`` — forbidden submodule pulled as a name from the
        parent package.
      * any ``from … import SseServerTransport / sse_app / streamable_http_app``
        — forbidden transport NAME regardless of source module.
    """

    def __init__(self) -> None:
        self.findings: list[tuple[int, str]] = []

    # `import mcp.server.sse` / `import mcp.server.sse as transport`
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _is_forbidden_module_path(alias.name):
                self.findings.append(
                    (
                        node.lineno,
                        f"import {alias.name!r}"
                        + (f" as {alias.asname!r}" if alias.asname else "")
                        + " — non-stdio MCP transport forbidden; "
                        "use mcp.server.stdio (P2-I4)",
                    )
                )
        self.generic_visit(node)

    # `from mcp.server.sse import …` / `from mcp.server import sse` /
    # `from anywhere import SseServerTransport`
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # Relative imports (level > 0) never reach `mcp.server.*` (a third-party
        # package), so only absolute imports are considered for the module-path
        # check. Forbidden NAMES are still checked below regardless of level.
        if node.level == 0 and node.module is not None:
            module = node.module
            if _is_forbidden_module_path(module):
                names = ", ".join(alias.name for alias in node.names) or "*"
                self.findings.append(
                    (
                        node.lineno,
                        f"from {module} import {names} — "
                        "non-stdio MCP transport module forbidden; "
                        "use mcp.server.stdio (P2-I4)",
                    )
                )
            elif module == "mcp.server":
                # `from mcp.server import sse` / `… import streamable_http`
                for alias in node.names:
                    if _is_forbidden_server_submodule(alias.name):
                        self.findings.append(
                            (
                                node.lineno,
                                f"from mcp.server import {alias.name}"
                                + (f" as {alias.asname}" if alias.asname else "")
                                + " — non-stdio MCP transport submodule forbidden; "
                                "use mcp.server.stdio (P2-I4)",
                            )
                        )

        # Forbidden transport NAMES — flagged no matter the source module so a
        # re-export shim (`from mypkg.compat import SseServerTransport`) cannot
        # smuggle a non-stdio transport past the module-path check.
        for alias in node.names:
            if alias.name in _FORBIDDEN_TRANSPORT_NAMES:
                src = node.module if node.module else "."
                self.findings.append(
                    (
                        node.lineno,
                        f"from {src} import {alias.name}"
                        + (f" as {alias.asname}" if alias.asname else "")
                        + " — non-stdio MCP transport entry point forbidden; "
                        "use mcp.server.stdio (P2-I4)",
                    )
                )
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def _scan_file(path: Path, *, raise_on_syntax: bool = False) -> list[Violation]:
    """Return MCP001 violations found in *path*.

    Per-line ``# noqa: MCP001 <reason>`` suppresses a single finding; duplicate
    findings on the same physical line collapse into one entry. ``SyntaxError``
    surfaces as an empty result by default (mypy --strict would fail first);
    set ``raise_on_syntax=True`` to fail loudly (used by the self-test).
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
    visitor = _TransportVisitor()
    visitor.visit(tree)

    seen: set[int] = set()
    violations: list[Violation] = []
    for lineno, message in visitor.findings:
        if lineno in seen:
            continue
        source_line = lines[lineno - 1] if lineno <= len(lines) else ""
        if has_noqa(source_line, "MCP001"):
            continue
        seen.add(lineno)
        violations.append(Violation(file=path, lineno=lineno, rule="MCP001", message=message))
    return violations


def _file_contains_transport_node(path: Path) -> bool:
    """Return True iff *path* contains at least one forbidden-transport AST node.

    Used by ``--self-test`` to assert ``clean/`` fixtures actually exercise the
    suppression path (i.e. the file DOES contain a forbidden import, but each is
    silenced via ``# noqa: MCP001 <reason>``). Walks the AST IGNORING noqa.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return False
    visitor = _TransportVisitor()
    visitor.visit(tree)
    return bool(visitor.findings)


def _is_test_file(path: Path) -> bool:
    """True for co-located ``test_*.py`` / ``conftest.py`` files inside src/ trees.

    Convention pinned to ``test_*.py`` + ``conftest.py`` (matches pytest
    discovery). Test files may legitimately import non-stdio transports (e.g. to
    assert they are NOT mounted, or to exercise upstream behaviour) and are
    exempt, mirroring the broader ``tests/`` exclusion.
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
    """Exercise bundled fixtures and assert MCP001 detection works.

    ``clean/`` fixtures must contain at least one forbidden-transport AST node
    so the suppression path is actually exercised (a clean fixture with no
    forbidden import at all would vacuously pass). ``violations/`` fixtures must
    each surface at least one MCP001.
    """
    fixture_root = SCRIPTS_DIR / "checks" / "fixtures" / "mcp_transport"
    failures: list[str] = []

    def _list_fixture_files(directory: Path) -> list[Path]:
        return sorted(
            p
            for p in walk_python_files([directory], skip_dirs=DEFAULT_SKIP_DIRS)
            if not p.name.startswith("_")
        )

    # Clean fixtures — every forbidden import is suppressed via # noqa: MCP001 <reason>.
    clean_dir = fixture_root / "clean"
    clean_files: list[Path] = []
    if not clean_dir.exists():
        failures.append(f"Missing clean fixtures directory: {clean_dir}")
    else:
        clean_files = _list_fixture_files(clean_dir)
        if not clean_files:
            failures.append(f"Empty clean fixtures directory: {clean_dir}")
        for fpath in clean_files:
            if not _file_contains_transport_node(fpath):
                failures.append(
                    f"FAIL clean/{fpath.name}: contains no forbidden-transport "
                    "AST nodes — suppression path not exercised"
                )
            viols = _scan_file(fpath)
            for v in viols:
                failures.append(
                    f"FAIL clean/{v.file.name}:{v.lineno}: unexpected MCP001: {v.message}"
                )

    # Violation fixtures — every file MUST surface at least one MCP001.
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
                failures.append(f"FAIL violations/{fpath.name}: expected MCP001 but got none")

    if failures:
        print("check_mcp_transport.py --self-test FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    total = len(clean_files) + len(violation_files)
    print(f"✓ check_mcp_transport.py self-test OK ({total} fixtures, 0 failures)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_mcp_transport.py",
        description="Enforce Phase-2 invariant P2-I4 'MCP transport stdio-only'.",
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
            f"\nmcp-transport: {len(violations)} violation(s) in {scanned} file(s) scanned.",
            file=sys.stderr,
        )
        return 1

    if args.verbose:
        print(f"✓ mcp-transport OK ({scanned} files scanned, 0 violations)")
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
