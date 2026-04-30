#!/usr/bin/env python3
"""check_no_subprocess.py — enforce NFR-S5 "no shell on the request path".

CI gate (Story 3.8): walks the spine + bot source trees and rejects any
``import subprocess`` / ``from subprocess import …`` / ``os.system`` /
``os.popen`` / ``os.exec*`` / ``subprocess.<anything>`` attribute access.

Hardened detection (Story 3.8 review H1/H2/H3/M17):

  * H1 — aliased imports tracked: ``import subprocess as sp`` followed by
    ``sp.run(...)``; ``from subprocess import run as r`` followed by ``r(...)``;
    ``from subprocess import Popen as P`` followed by ``P(...)``.
  * H2 — ``from os import system`` followed by bare ``system(...)`` flagged
    via the from-imported-shell-name set.
  * H3 — dynamic imports caught: ``getattr(os, "system")``,
    ``__import__("subprocess")``, ``importlib.import_module("subprocess")``.
    Reflective ``eval``/``exec`` shell access remains intentionally
    out of scope per spec (Story 3.8 N1).
  * M17 — ``execlpe`` removed from the os-shell attr set; the canonical
    Python list is ``{exec, execv, execve, execvp, execvpe, execl, execle,
    execlp}`` — no ``execlpe``.

Suppression: ``# noqa: SHELL001 <reason>`` on the offending line. The
reason MUST be non-empty (matches ``checks._common._NOQA_RE``); a bare
``# noqa: SHELL001`` is rejected. Story 5.4 will use this exemption to
opt-in the Claude Code CLI supervision call site. Multiple tags on the
same line are supported (Story 3.8 review H9): ``# noqa: PLC0415, SHELL001 — reason``.

Spine-only walk (request-path scope):
  - INCLUDED (Story 3.8 review M12/M13: glob-discover all current spine
    members under services/, mcp-servers/, packages/):
      services/*/src/         (telegram-gateway, registry-api,
                               registry-state, clawhip-daemon,
                               worker-wrapper, console-cli, …)
      mcp-servers/*/src/      (clawhip-bridge, task-registry,
                               session-registry, …)
      packages/*/src/         (events, idempotency, secret-hygiene,
                               orchestrator-adapter, …)
  - EXCLUDED:
      tests/                    (fuzz harness monkeypatches subprocess by design)
      scripts/migrator/         (operator tool, not on request path)
      scripts/sync_upstream.py  (operator tool, not on request path)
      upstream/                 (vendored)
      anything outside the included list

Note (Story 5.4 forward-compat): when the worker-wrapper Claude Code CLI
supervision call site lands, it MUST carry ``# noqa: SHELL001 — supervises
Claude Code CLI per FR3`` and any test files within the spine remain
exempt via the ``_is_test_file`` filter.

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
#
# Story 3.8 review M17: the canonical ``os`` exec family is
# ``{exec, execv, execve, execvp, execvpe, execl, execle, execlp}``. There is
# NO ``os.execlpe`` — it was an erroneous entry; removed.
#
# Story 3.8 review L17 (deferred): ``posix_spawn``, ``spawnl``, ``spawnv``,
# ``spawnvp``, ``forkpty``, ``fork``, ``pty.spawn`` are NOT in this set today
# (out of scope per spec). If the threat model is ever loosened, add them
# here AND extend the runtime guard in `_subprocess_guard` correspondingly.
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
    }
)

# ---------------------------------------------------------------------------
# Spine-only scan roots (request-path contract)
# ---------------------------------------------------------------------------


def _discover_spine_roots() -> list[Path]:
    """Glob-discover every workspace src/ tree under the spine directories.

    Story 3.8 review M12/M13: rather than maintain a hand-curated list (which
    silently exempts new services like ``worker-wrapper``, ``console-cli``,
    or new mcp-servers), iterate the workspace layout. The current MVP has
    ZERO subprocess use across all of these trees, so widening the gate is
    safe today and tightens it retroactively for any future commit.
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


_SPINE_ROOTS: list[Path] = _discover_spine_roots()

# Per-walk skip set — DEFAULT_SKIP_DIRS already covers __pycache__ + caches +
# vendored trees + AI-tool dotdirs. We additionally skip ``tests`` so co-located
# test files inside spine src/ trees are exempt from the SHELL001 rule (the
# fuzz harness itself imports ``subprocess`` to monkeypatch it).
#
# Story 3.8 review L13: ``fixtures`` is included for the same reason — any
# in-spine fixture trees (test data) shouldn't be scanned. If a future
# ``services/foo/src/fixtures/`` ever needed scanning, this would need a
# more precise filter (e.g. only skip the top-level scripts/checks/fixtures/).
_SPINE_SKIP: frozenset[str] = DEFAULT_SKIP_DIRS | frozenset({"tests", "fixtures"})


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------


class _ShellVisitor(ast.NodeVisitor):
    """Collect ``(lineno, message)`` tuples for every SHELL001 candidate.

    Stateful tracking (Story 3.8 review H1/H2):
      * ``_subprocess_aliases`` — names bound by ``import subprocess as <alias>``
        (alias is whatever comes after ``as``; the bare name ``subprocess``
        is also tracked when no alias was used).
      * ``_subprocess_from_names`` — names bound by ``from subprocess import
        <name> [as <alias>]`` (so bare ``run(...)`` after such an import
        still gets flagged).
      * ``_os_shell_imports`` — names bound by ``from os import system`` (or
        any name in ``_OS_SHELL_ATTRS``); bare ``system(...)`` is flagged.
    """

    def __init__(self) -> None:
        self.findings: list[tuple[int, str]] = []
        self._subprocess_aliases: set[str] = set()
        self._subprocess_from_names: set[str] = set()
        self._os_shell_imports: set[str] = set()

    # `import subprocess` / `import subprocess as sp`
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            # Story 3.8 review L1: ``subprocess.foo`` startswith dead branch
            # removed — ``subprocess`` is a leaf module.
            if alias.name == "subprocess":
                self._subprocess_aliases.add(alias.asname or alias.name)
                self.findings.append(
                    (
                        node.lineno,
                        f"import {alias.name!r}"
                        + (f" as {alias.asname!r}" if alias.asname else "")
                        + " — subprocess is forbidden on the request path (NFR-S5)",
                    )
                )
        self.generic_visit(node)

    # `from subprocess import run, Popen, ...` / `from os import system`
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # Story 3.8 review L2: relative imports of subprocess/os are
        # vanishingly unlikely (subprocess/os are stdlib, never re-exported
        # from an in-repo package), so the level-0 guard is documented here
        # and retained — it just narrows the false-positive surface.
        if node.level != 0:
            self.generic_visit(node)
            return

        if node.module == "subprocess":
            names = ", ".join(alias.name for alias in node.names) or "*"
            for alias in node.names:
                self._subprocess_from_names.add(alias.asname or alias.name)
            self.findings.append(
                (
                    node.lineno,
                    f"from subprocess import {names} — "
                    "subprocess is forbidden on the request path (NFR-S5)",
                )
            )
        elif node.module == "os":
            for alias in node.names:
                if alias.name in _OS_SHELL_ATTRS:
                    bound_name = alias.asname or alias.name
                    self._os_shell_imports.add(bound_name)
                    self.findings.append(
                        (
                            node.lineno,
                            f"from os import {alias.name}"
                            + (f" as {alias.asname}" if alias.asname else "")
                            + " — shell entry point forbidden on the request path (NFR-S5)",
                        )
                    )
        self.generic_visit(node)

    # `subprocess.run(...)` / `os.system(...)` / `<alias>.run(...)`
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name):
            name_id = node.value.id
            # Story 3.8 review H1: aliased subprocess access (``sp.run(...)``)
            # caught via the aliased-name set. Bare ``subprocess`` is in the set
            # too when ``import subprocess`` was used without ``as``.
            if name_id in self._subprocess_aliases:
                self.findings.append(
                    (
                        node.lineno,
                        f"{name_id}.{node.attr}(...) — "
                        f"subprocess (imported as {name_id!r}) is forbidden "
                        "on the request path (NFR-S5)",
                    )
                )
            elif name_id == "subprocess":
                # Defensive duplicate of the alias path — covers ``subprocess.run(...)``
                # in files that never explicitly imported subprocess (e.g. via a
                # builtin import shadow). Cheap; keeps detection robust.
                self.findings.append(
                    (
                        node.lineno,
                        f"subprocess.{node.attr}(...) — "
                        "subprocess is forbidden on the request path (NFR-S5)",
                    )
                )
            elif name_id == "os" and node.attr in _OS_SHELL_ATTRS:
                self.findings.append(
                    (
                        node.lineno,
                        f"os.{node.attr}(...) — "
                        "shell entry point forbidden on the request path (NFR-S5)",
                    )
                )
        self.generic_visit(node)

    # Story 3.8 review H2/H3: bare ``run(...)`` after ``from subprocess import
    # run``; ``getattr(os, "system")``; ``__import__("subprocess")``;
    # ``importlib.import_module("subprocess")``.
    def visit_Call(self, node: ast.Call) -> None:
        # Bare-name calls bound by `from subprocess import …` / `from os import system`.
        if isinstance(node.func, ast.Name):
            name_id = node.func.id
            if name_id in self._subprocess_from_names:
                self.findings.append(
                    (
                        node.lineno,
                        f"{name_id}(...) — "
                        f"subprocess.{name_id} (imported via `from subprocess`) "
                        "is forbidden on the request path (NFR-S5)",
                    )
                )
            elif name_id in self._os_shell_imports:
                self.findings.append(
                    (
                        node.lineno,
                        f"{name_id}(...) — "
                        f"os shell entry point (imported via `from os`) "
                        "is forbidden on the request path (NFR-S5)",
                    )
                )
            # Dynamic-import vectors: __import__("subprocess") / __import__("os")
            elif name_id == "__import__":
                if (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and node.args[0].value in {"subprocess", "os"}
                ):
                    self.findings.append(
                        (
                            node.lineno,
                            f"__import__({node.args[0].value!r}) — "
                            "dynamic shell-module import forbidden on the request "
                            "path (NFR-S5)",
                        )
                    )
            # getattr(os, "system") / getattr(subprocess, "run") with a literal arg.
            elif (
                name_id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and (
                    node.args[0].id == "subprocess"
                    or (node.args[0].id == "os" and node.args[1].value in _OS_SHELL_ATTRS)
                )
            ):
                target = node.args[0].id
                attr = node.args[1].value
                self.findings.append(
                    (
                        node.lineno,
                        f"getattr({target}, {attr!r}) — "
                        "reflective shell-attribute lookup forbidden "
                        "on the request path (NFR-S5)",
                    )
                )
        # importlib.import_module("subprocess")
        elif isinstance(node.func, ast.Attribute):
            if (
                node.func.attr == "import_module"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value in {"subprocess", "os"}
            ):
                self.findings.append(
                    (
                        node.lineno,
                        f"importlib.import_module({node.args[0].value!r}) — "
                        "dynamic shell-module import forbidden on the request "
                        "path (NFR-S5)",
                    )
                )
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def _scan_file(path: Path, *, raise_on_syntax: bool = False) -> list[Violation]:
    """Return SHELL001 violations found in *path*.

    Per-line ``# noqa: SHELL001 <reason>`` suppresses a single finding;
    duplicate findings on the same physical line collapse into one entry to
    avoid spamming the operator with N copies of the same Attribute hit.

    Story 3.8 review L15: SyntaxError surfaces as an empty-violations result
    by default (a parse error in spine src/ would fail mypy --strict before
    this gate runs); ``--verbose`` mode could be extended to log it. Set
    ``raise_on_syntax=True`` to fail loudly (used by self-test).
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


def _file_contains_shell_node(path: Path) -> bool:
    """Return True iff *path* contains at least one shell-relevant AST node.

    Used by ``--self-test`` (Story 3.8 review M11) to assert ``clean/``
    fixtures actually exercise the suppression path (i.e. the fixture file
    DOES contain a subprocess/os.system/etc. call, but each is silenced via
    ``# noqa: SHELL001 <reason>``). Walks the AST IGNORING noqa suppression.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return False
    visitor = _ShellVisitor()
    visitor.visit(tree)
    return bool(visitor.findings)


def _is_test_file(path: Path) -> bool:
    """True for co-located ``test_*.py`` / ``conftest.py`` files inside spine src/.

    Story 3.8 review L18: convention pinned to ``test_*.py`` + ``conftest.py``.
    Pytest's discovery is configured the same way in pyproject.toml; if the
    convention diverges, update both this helper AND the pytest config.

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
    """Exercise bundled fixtures and assert SHELL001 detection works.

    Story 3.8 review M11: ``clean/`` fixtures must contain at least one
    shell-relevant AST node so the suppression path is actually exercised
    (a clean fixture with no subprocess at all would vacuously pass).
    Story 3.8 review L4: each fixture directory must contain at least one
    fixture file (an empty dir would vacuously pass).
    Story 3.8 review L16: use ``walk_python_files`` for file enumeration in
    both clean and violations dirs (matches the scan's recursive behavior).
    Story 3.8 review L5: skip ``_*.py`` files symmetrically in BOTH dirs.
    """
    fixture_root = SCRIPTS_DIR / "checks" / "fixtures" / "no_subprocess"
    failures: list[str] = []

    def _list_fixture_files(directory: Path) -> list[Path]:
        return sorted(
            p
            for p in walk_python_files([directory], skip_dirs=DEFAULT_SKIP_DIRS)
            if not p.name.startswith("_")
        )

    # Clean fixtures — every file is suppressed via # noqa: SHELL001 <reason>.
    clean_dir = fixture_root / "clean"
    clean_files: list[Path] = []
    if not clean_dir.exists():
        failures.append(f"Missing clean fixtures directory: {clean_dir}")
    else:
        clean_files = _list_fixture_files(clean_dir)
        if not clean_files:
            failures.append(f"Empty clean fixtures directory: {clean_dir}")
        for fpath in clean_files:
            # Story 3.8 review M11: clean fixture MUST contain at least one
            # shell node so the suppression path is exercised.
            if not _file_contains_shell_node(fpath):
                failures.append(
                    f"FAIL clean/{fpath.name}: contains no subprocess/os.system "
                    "AST nodes — suppression path not exercised"
                )
            viols = _scan_file(fpath)
            for v in viols:
                failures.append(
                    f"FAIL clean/{v.file.name}:{v.lineno}: unexpected SHELL001: {v.message}"
                )

    # Violation fixtures — every file MUST surface at least one SHELL001.
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
                failures.append(f"FAIL violations/{fpath.name}: expected SHELL001 but got none")

    if failures:
        print("check_no_subprocess.py --self-test FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    total = len(clean_files) + len(violation_files)
    print(f"✓ check_no_subprocess.py self-test OK ({total} fixtures, 0 failures)")
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
        # Story 3.8 review L14: per-root file-count breakdown mirrors
        # check_imports.py's verbose mode.
        print(f"✓ no-subprocess OK ({scanned} files scanned, 0 violations)")
        for root in _SPINE_ROOTS:
            count = sum(1 for _ in walk_python_files([root], skip_dirs=_SPINE_SKIP))
            try:
                rel = root.relative_to(REPO_ROOT)
            except ValueError:
                rel = root
            print(f"    {rel}: {count} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
