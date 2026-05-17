"""Ratchet test: registry-state must not spawn subprocesses without
trace_id propagation.

Story 9.6 review pass-2 PH8 / pass-3 TH3: pass-1 H0 was marked complete
because no spawn site existed in registry-state at the time, but
absence-of-evidence is not evidence-of-absence.  This test asserts that
registry-state stays free of process-spawn primitives — if a future
commit adds one, the ratchet trips and the author is reminded to
propagate ``WORKER_TRACE_ID`` through the spawn env (mirroring Story
9.6 PH0's ``OMCRunner`` fix in orchestrator-adapter).

Pass-3 TH3 rewrite: replace the text-regex with an AST walk that
resolves ``ast.Call.func`` chains to dotted names AND ``from X import Y
as Z`` aliases.  Self-test fixture confirms the walker catches aliased
imports.  Test files (``test_*.py``) are excluded from the scan so the
ratchet's own regex string in this file (or fixture strings used to
self-test) do not trip the rule.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

# Forbidden dotted names — any ``ast.Call`` whose resolved func name matches
# any prefix here trips the ratchet.  Module prefix listed before the
# attribute so e.g. ``subprocess.check_output`` matches without also matching
# bare ``check_output`` (which would have false-positives on local helpers).
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

# Bare-name primitives reachable via ``from X import Y`` — these trip when
# the call is ``Popen(...)`` after ``from subprocess import Popen``.  Note:
# the AST walker maps aliased imports back to the canonical dotted name
# via ``_import_aliases`` so we still match against ``_FORBIDDEN_DOTTED``.
_FORBIDDEN_FROM_IMPORT: dict[str, str] = {
    # bare-name → canonical dotted form
    "Popen": "subprocess.Popen",
    "create_subprocess_exec": "asyncio.create_subprocess_exec",
    "create_subprocess_shell": "asyncio.create_subprocess_shell",
    "check_output": "subprocess.check_output",
    "check_call": "subprocess.check_call",
    "getoutput": "subprocess.getoutput",
    "getstatusoutput": "subprocess.getstatusoutput",
    # Note: ``run`` and ``call`` are too generic to trip on bare-name import.
}


class _SpawnVisitor(ast.NodeVisitor):
    """AST walker that detects forbidden spawn primitives.

    Resolves:
    * ``module.attr(...)`` chains via ``ast.Attribute`` -> dotted name.
    * ``from X import Y as Z; Z(...)`` via alias map built from
      ``ast.ImportFrom``.
    * ``import M as A; A.attr(...)`` via alias map from ``ast.Import``.
    """

    def __init__(self) -> None:
        # Map alias-name → canonical-name. Built from ``from X import Y as Z``
        # and ``import M as A`` statements at module top.
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
            # Resolve to canonical dotted form when known.
            canonical = _FORBIDDEN_FROM_IMPORT.get(bare)
            canonical = f"{mod}.{bare}" if canonical is None and mod else canonical or bare
            self._import_aliases[local] = canonical
        self.generic_visit(node)

    def _resolve_call_name(self, node: ast.expr) -> str | None:
        """Resolve a Call.func node to its dotted canonical name, if any."""
        if isinstance(node, ast.Name):
            # Bare name — look up alias map; fall back to bare name.
            return self._import_aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            parts: list[str] = []
            cur: ast.expr = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                # Resolve leftmost name through alias map.
                leftmost = self._import_aliases.get(cur.id, cur.id)
                parts.append(leftmost)
                return ".".join(reversed(parts))
        return None

    def visit_Call(self, node: ast.Call) -> None:
        name = self._resolve_call_name(node.func)
        if name is not None and name in _FORBIDDEN_DOTTED:
            self.offenders.append((node.lineno, name))
        self.generic_visit(node)


def _scan_source(source: str) -> list[tuple[int, str]]:
    """Return forbidden-call offenders for ``source``."""
    tree = ast.parse(source)
    visitor = _SpawnVisitor()
    visitor.visit(tree)
    return visitor.offenders


# This file lives at ``services/registry-state/src/registry_state/test_no_subprocess_spawn.py``.
# Scan the ``registry_state`` package directory (siblings of this file).
_SRC_ROOT = Path(__file__).parent


def test_no_subprocess_spawn_in_registry_state() -> None:
    """If you're adding a spawn site, also propagate ``WORKER_TRACE_ID`` via
    env (Epic 9 / Story 9.6 PH0 / PH8 / TH3).

    Pass-3 TH3: exclude ``test_*.py`` files — co-located test fixtures
    (e.g. monkeypatch-based emulation, fixture strings) are allowed to
    name spawn primitives without tripping the ratchet.  Production code
    in registry-state must remain spawn-free.
    """
    offenders: list[str] = []
    for py in _SRC_ROOT.rglob("*.py"):
        # Skip this test file itself — its dotted-name strings are literals.
        if py.resolve() == Path(__file__).resolve():
            continue
        # Skip co-located test files (Story 9.6 review pass-3 TH3).
        if py.name.startswith("test_"):
            continue
        try:
            text = py.read_text(encoding="utf-8")
            calls = _scan_source(text)
        except SyntaxError:
            continue
        if calls:
            for lineno, name in calls:
                offenders.append(f"{py.relative_to(_SRC_ROOT)}:{lineno} -> {name}")
    assert not offenders, (
        "Registry-state must not spawn subprocesses (Story 9.6 PH0/PH8/TH3): "
        f"{offenders}. If you're adding a worker spawn site, propagate "
        "WORKER_TRACE_ID via env (see orchestrator-adapter/OMCRunner for "
        "the canonical pattern)."
    )


def test_ast_walker_self_test_aliased_import_detected() -> None:
    """TH3 self-test: ``from subprocess import Popen as _foo; _foo(...)``
    is detected by the AST walker (the prior text-regex would have missed).
    """
    fixture = textwrap.dedent(
        """
        from subprocess import Popen as _foo
        _foo("/bin/true")
        """
    )
    calls = _scan_source(fixture)
    assert calls, "Aliased Popen import should be flagged by AST walker"
    assert any(name == "subprocess.Popen" for _, name in calls)


def test_ast_walker_self_test_os_system_detected() -> None:
    """TH3 self-test: ``os.system`` is detected (missed by the old regex)."""
    fixture = textwrap.dedent(
        """
        import os
        os.system("ls")
        """
    )
    calls = _scan_source(fixture)
    assert any(name == "os.system" for _, name in calls)


def test_ast_walker_self_test_multiprocessing_process_detected() -> None:
    """TH3 self-test: ``multiprocessing.Process(...)`` detected."""
    fixture = textwrap.dedent(
        """
        import multiprocessing
        multiprocessing.Process(target=lambda: None)
        """
    )
    calls = _scan_source(fixture)
    assert any(name == "multiprocessing.Process" for _, name in calls)


def test_ast_walker_self_test_subprocess_check_output_detected() -> None:
    """TH3 self-test: ``subprocess.check_output`` detected."""
    fixture = textwrap.dedent(
        """
        import subprocess
        subprocess.check_output(["ls"])
        """
    )
    calls = _scan_source(fixture)
    assert any(name == "subprocess.check_output" for _, name in calls)


def test_ast_walker_self_test_clean_code_passes() -> None:
    """TH3 self-test: code without forbidden calls is silent."""
    fixture = textwrap.dedent(
        """
        from pathlib import Path
        Path("/tmp").exists()
        """
    )
    calls = _scan_source(fixture)
    assert calls == []
