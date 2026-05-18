"""Project-wide ratchet: every process-spawn primitive must appear in the allowlist.

Story 9.7 / AC12: extends the registry-state-scoped ratchet (pass-3 TH3,
services/registry-state/src/registry_state/test_no_subprocess_spawn.py) to
cover all of services/, mcp-servers/, and packages/.

If you need to add a new spawn site:
1. Add it to ``_ALLOWLIST`` below with a brief justification.
2. Propagate ``WORKER_TRACE_ID`` (or equivalent) through the spawn's env dict
   so the trace_id crosses the process boundary (Epic 9 / FR59).
3. Open a PR referencing this file in the review checklist.

Without an allowlist entry, the CI gate fails — forcing authors to make the
spawn site visible and documented.

Pattern mirrors registry-state/test_no_subprocess_spawn.py (pass-3 TH3 AST
walk, alias-aware, test files excluded from the scan).
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Forbidden dotted names — matches subprocess / asyncio / os / multiprocessing /
# pty spawn primitives. Mirror of the registry-state ratchet.
# ---------------------------------------------------------------------------

_FORBIDDEN_DOTTED: frozenset[str] = frozenset(
    {
        # subprocess module surface
        "subprocess.Popen",
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_output",
        "subprocess.check_call",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
        # asyncio subprocess
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        # os module surface — process spawn / fork primitives
        "os.system",
        "os.popen",
        "os.fork",
        "os.forkpty",
        "os.execl",
        "os.execle",
        "os.execlp",
        "os.execlpe",
        "os.execv",
        "os.execve",
        "os.execvp",
        "os.execvpe",
        "os.spawnl",
        "os.spawnle",
        "os.spawnlp",
        "os.spawnlpe",
        "os.spawnv",
        "os.spawnve",
        "os.spawnvp",
        "os.spawnvpe",
        # multiprocessing module — process objects
        "multiprocessing.Process",
        "multiprocessing.Pool",
        # pty — fork primitive
        "pty.fork",
    }
)

# Bare-name → canonical dotted form for ``from X import Y [as Z]`` imports.
_FROM_IMPORT_MAP: dict[str, str] = {
    "Popen": "subprocess.Popen",
    "create_subprocess_exec": "asyncio.create_subprocess_exec",
    "create_subprocess_shell": "asyncio.create_subprocess_shell",
    "check_output": "subprocess.check_output",
    "check_call": "subprocess.check_call",
    "getoutput": "subprocess.getoutput",
    "getstatusoutput": "subprocess.getstatusoutput",
    # Note: ``run`` and ``call`` omitted — too generic for bare-name detection.
}

# ---------------------------------------------------------------------------
# Allowlist — every known legitimate spawn site.
#
# Key format: relative path from repo root (str) → set of allowed line numbers.
# Line numbers may drift on edits; when a ratchet trip references a stale line,
# re-run the scan to find the new line and update the allowlist entry.
# ---------------------------------------------------------------------------

_REPO_ROOT: Path = Path(__file__).resolve().parents[1]


def _rel(p: str) -> str:
    """Return p as a POSIX relative path string (canonical key form)."""
    return str(Path(p).as_posix())


# Each value is a set of allowed lines for that file.
# Format: { rel_path: {line, ...} }
_ALLOWLIST: dict[str, set[int]] = {
    # worker-wrapper: spawns Claude Code subprocess.
    # Story 9.6 — propagates WORKER_TRACE_ID through env (FR59 / PH0).
    _rel("services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py"): {151},
    # orchestrator-adapter: spawns OMC node subprocess.
    # Story 9.6 — propagates OMB_TRACE_ID through env (FR59 / TH3).
    _rel("services/orchestrator-adapter/src/orchestrator_adapter/adapters/omc_runner.py"): {93},
    # sync_upstream.py: dev-only maintenance script — clones upstream repos
    # into scripts/upstream/ for vendored-source tracking. Not invoked at
    # runtime, has no trace_id context (operator-local one-shot tool).
    # Story 9.7 / AC12 PH-B7/B8/E5: scripts/ added to _SCAN_ROOTS.
    _rel("scripts/sync_upstream.py"): {76, 93},
}


# ---------------------------------------------------------------------------
# AST walker (mirrors registry-state ratchet, pass-3 TH3)
# ---------------------------------------------------------------------------


class _SpawnVisitor(ast.NodeVisitor):
    """AST walker that detects forbidden spawn primitives."""

    def __init__(self) -> None:
        self._import_aliases: dict[str, str] = {}
        self.offenders: list[tuple[int, str]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            canonical = alias.name
            local = alias.asname or alias.name
            self._import_aliases[local] = canonical
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        for alias in node.names:
            bare = alias.name
            local = alias.asname or alias.name
            canonical = _FROM_IMPORT_MAP.get(bare)
            canonical = f"{mod}.{bare}" if canonical is None and mod else canonical or bare
            self._import_aliases[local] = canonical
        self.generic_visit(node)

    def _resolve_call_name(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return self._import_aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            parts: list[str] = []
            cur: ast.expr = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                leftmost = self._import_aliases.get(cur.id, cur.id)
                parts.append(leftmost)
                return ".".join(reversed(parts))
        return None

    def visit_Call(self, node: ast.Call) -> None:
        name = self._resolve_call_name(node.func)
        if name is not None and name in _FORBIDDEN_DOTTED:
            self.offenders.append((node.lineno, name))
        self.generic_visit(node)


def _scan_file(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    visitor = _SpawnVisitor()
    visitor.visit(tree)
    return visitor.offenders


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

# Story 9.7 pass-1 PH-B7/B8/E5: extend scan roots to include scripts/, tools/,
# and root-level *.py files. Previously only services/mcp-servers/packages were
# scanned; scripts/emit_signature_rejected.py and similar were invisible.
_SCAN_ROOTS: tuple[str, ...] = ("services", "mcp-servers", "packages", "scripts", "tools")

# Directory-names that mark test trees — files under any directory named one of
# these are excluded from the spawn-site scan. This is more robust than
# py.name.startswith("test_") which missed files like `conftest.py`, files in
# `tests/` sub-trees, and co-located `test_*.py` inside src packages.
# PH-B7: use directory-based exclusion instead of filename prefix.
#
# Story 9.7 pass-2 TM-E6: ``fixtures`` is intentionally excluded from this
# set — a blanket fixtures-exclusion would hide production files under any
# ``fixtures/`` directory (e.g. ``services/foo/src/foo/fixtures/loader.py``).
# Instead, ``fixtures/`` directories are excluded ONLY when their parent
# is ``tests/`` or ``test/`` (see :func:`_is_test_file`).
_TEST_DIR_NAMES: frozenset[str] = frozenset({"tests", "test"})

# Explicit path-level exclusion list for non-test fixtures directories that
# nonetheless ship spawn-related sample code for ruff/lint gates.
_EXPLICIT_FIXTURE_PATHS: frozenset[str] = frozenset(
    {
        # SHELL001 ruff-plugin samples — positive/negative fixtures, not prod.
        "scripts/checks/fixtures",
    }
)

# Wildcard-import limitation: ``from subprocess import *`` followed by
# ``Popen(...)`` is NOT detected. The bare-name mapping in _FROM_IMPORT_MAP only
# covers explicit ``from X import Y`` forms. Document this known gap.
# Real code should avoid wildcard imports (Ruff PEP-8 rule W0401/F403).


def _is_test_file(py: Path) -> bool:
    """Return True if *py* lives under a test directory OR is a test file.

    Excludes:
      * any file whose parent directory is named "tests" or "test"
      * any file named test_*.py (co-located unit tests inside src packages)
      * conftest.py files (pytest fixtures, not production code)
      * Story 9.7 pass-2 TM-E6: ``fixtures/`` directories ONLY when nested
        under a ``tests/``/``test/`` ancestor, plus an explicit allowlist
        for non-test fixture trees (``scripts/checks/fixtures``).
    """
    parts = py.parts
    # Check parent directories for tests/test
    for part in parts:
        if part in _TEST_DIR_NAMES:
            return True
    # Filename heuristics
    if py.name.startswith("test_") or py.name == "conftest.py":
        return True
    # Fixtures handling — nested fixtures under tests/test count, AND a small
    # explicit list of non-test fixture dirs that ship sample-code for gates.
    if "fixtures" in parts:
        try:
            rel = str(py.relative_to(_REPO_ROOT).as_posix())
        except ValueError:
            rel = str(py.as_posix())
        for explicit in _EXPLICIT_FIXTURE_PATHS:
            if rel.startswith(explicit + "/"):
                return True
    return False


def test_no_undocumented_spawn_sites() -> None:
    """Every spawn site in services/, mcp-servers/, packages/, scripts/, tools/
    and root-level *.py files must be in the allowlist, or this gate fails.

    If you are adding a legitimate spawn site:
      1. Add it to _ALLOWLIST in this file with a justification comment.
      2. Propagate trace_id through the subprocess env (Epic 9 / FR59).
      3. Update the allowlist line number if the code was refactored.

    Story 9.7 / AC12 — project-wide spawn-site visibility gate.
    PH-B7/B8/E5: extended from services/mcp-servers/packages to also cover
    scripts/, tools/, and root-level *.py files.
    """
    undocumented: list[str] = []

    # Scan rooted subdirectories
    for root_name in _SCAN_ROOTS:
        root = _REPO_ROOT / root_name
        if not root.exists():
            continue
        for py in sorted(root.rglob("*.py")):
            if "__pycache__" in str(py):
                continue
            if _is_test_file(py):
                continue
            rel = str(py.relative_to(_REPO_ROOT).as_posix())
            hits = _scan_file(py)
            if not hits:
                continue
            allowed_lines = _ALLOWLIST.get(rel, set())
            for lineno, spawn_name in hits:
                if lineno not in allowed_lines:
                    undocumented.append(
                        f"{rel}:{lineno} -> {spawn_name}  "
                        f"[not in allowlist; add to _ALLOWLIST or remove the spawn]"
                    )

    # Also scan root-level *.py files (PH-B8: top-level scripts)
    for py in sorted(_REPO_ROOT.glob("*.py")):
        if "__pycache__" in str(py) or _is_test_file(py):
            continue
        rel = str(py.relative_to(_REPO_ROOT).as_posix())
        hits = _scan_file(py)
        if not hits:
            continue
        allowed_lines = _ALLOWLIST.get(rel, set())
        for lineno, spawn_name in hits:
            if lineno not in allowed_lines:
                undocumented.append(
                    f"{rel}:{lineno} -> {spawn_name}  "
                    f"[not in allowlist; add to _ALLOWLIST or remove the spawn]"
                )

    assert not undocumented, (
        "Undocumented spawn sites found — add to _ALLOWLIST in "
        "tests/test_no_undocumented_spawn_sites.py and propagate trace_id "
        "through the subprocess env (Epic 9 / FR59):\n" + "\n".join(f"  {s}" for s in undocumented)
    )


# ---------------------------------------------------------------------------
# Self-tests (mirror registry-state ratchet TH3 self-tests)
# ---------------------------------------------------------------------------


def test_ast_walker_detects_aliased_popen() -> None:
    """AST walker catches ``from subprocess import Popen as _foo; _foo(...)``."""
    fixture = textwrap.dedent(
        """
        from subprocess import Popen as _foo
        _foo("/bin/true")
        """
    )
    tree = ast.parse(fixture)
    v = _SpawnVisitor()
    v.visit(tree)
    assert v.offenders, "aliased Popen should be flagged"
    assert any(name == "subprocess.Popen" for _, name in v.offenders)


def test_ast_walker_detects_asyncio_create_subprocess() -> None:
    """AST walker catches ``asyncio.create_subprocess_exec``."""
    fixture = textwrap.dedent(
        """
        import asyncio
        await asyncio.create_subprocess_exec("ls")
        """
    )
    tree = ast.parse(fixture)
    v = _SpawnVisitor()
    v.visit(tree)
    assert any(name == "asyncio.create_subprocess_exec" for _, name in v.offenders)


def test_ast_walker_clean_code_passes() -> None:
    """AST walker is silent for code with no spawn primitives."""
    fixture = textwrap.dedent(
        """
        from pathlib import Path
        Path("/tmp").exists()
        """
    )
    tree = ast.parse(fixture)
    v = _SpawnVisitor()
    v.visit(tree)
    assert v.offenders == []


def test_allowlist_keys_are_valid_paths() -> None:
    """Every key in _ALLOWLIST must reference a file that exists in the repo."""
    missing = []
    for rel in _ALLOWLIST:
        if not (_REPO_ROOT / rel).is_file():
            missing.append(rel)
    assert not missing, "Stale allowlist entries (files no longer exist):\n" + "\n".join(
        f"  {m}" for m in missing
    )


def test_allowlisted_lines_contain_real_spawn_calls() -> None:
    """Story 9.7 pass-2 TM-E2: validate that every allowlisted (path, line)
    actually contains the expected spawn AST node.

    The ``(path, line_number)`` allowlist shape is fragile — any re-indent
    silently shifts line numbers and a non-spawn line can occupy the
    formerly-flagged number, masking a regression. Mitigate by asserting
    that the allowlisted line is in fact a spawn-call site (validated
    against the same AST walker used by the gate).
    """
    stale: list[str] = []
    for rel, lines in _ALLOWLIST.items():
        path = _REPO_ROOT / rel
        if not path.is_file():
            continue
        hits = {lineno for lineno, _name in _scan_file(path)}
        for line in lines:
            if line not in hits:
                stale.append(
                    f"{rel}:{line} — allowlisted but no spawn call on that line "
                    "(refactor drift; update _ALLOWLIST)"
                )
    assert not stale, "Stale allowlist line numbers:\n" + "\n".join(f"  {s}" for s in stale)


# PH-B8/E5 self-tests — verify new scan behaviour


def test_ast_walker_detects_from_subprocess_import_run() -> None:
    """PH-B8: ``from subprocess import run; run(...)`` IS flagged.

    The _FROM_IMPORT_MAP does NOT map 'run' → 'subprocess.run' (it's too
    generic a name), so this test documents the KNOWN GAP: bare ``run``
    after a star or named import without alias is not caught. The dotted
    form ``subprocess.run(...)`` IS caught (see below).
    """
    # Dotted form: always caught.
    fixture_dotted = textwrap.dedent(
        """
        import subprocess
        subprocess.run(["ls"])
        """
    )
    tree = ast.parse(fixture_dotted)
    v = _SpawnVisitor()
    v.visit(tree)
    assert any(name == "subprocess.run" for _, name in v.offenders), (
        "subprocess.run dotted form should be flagged"
    )


def test_is_test_file_excludes_test_directory() -> None:
    """PH-B7: directory-based exclusion catches files in tests/ sub-trees."""
    fake_test_dir = _REPO_ROOT / "services" / "foo" / "tests" / "test_bar.py"
    assert _is_test_file(fake_test_dir), "files under tests/ should be excluded"

    fake_test_dir2 = _REPO_ROOT / "services" / "foo" / "test" / "helpers.py"
    assert _is_test_file(fake_test_dir2), "files under test/ should be excluded"

    fake_prod = _REPO_ROOT / "services" / "foo" / "src" / "foo" / "bar.py"
    assert not _is_test_file(fake_prod), "src/ production files should not be excluded"


def test_is_test_file_excludes_conftest() -> None:
    """conftest.py is pytest fixture code, excluded from scan."""
    fake_conftest = _REPO_ROOT / "services" / "foo" / "src" / "conftest.py"
    assert _is_test_file(fake_conftest)


def test_scripts_root_spawn_would_be_flagged(tmp_path: Path) -> None:
    """PH-B8/E5: a Popen call in a scripts/ file is caught by the scanner.

    Uses a synthetic file — does not rely on a real scripts/*.py spawn.
    """
    from pathlib import Path as _Path  # noqa: PLC0415 — local import for test clarity

    fake_script = tmp_path / "emit_something.py"
    fake_script.write_text(
        textwrap.dedent(
            """
            import subprocess
            subprocess.Popen(["foo"])
            """
        )
    )
    hits = _scan_file(_Path(fake_script))
    assert hits, "Popen in a script file should be detected"
    assert any(name == "subprocess.Popen" for _, name in hits)
