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


# Each value is a dict mapping allowed line number → expected primitive
# dotted name (e.g. ``"subprocess.Popen"``). Pass-3 UH-6 tightened the
# shape from ``set[int]`` to ``dict[int, str]`` so a refactor that swaps
# one primitive for another at the same line (e.g. Popen → os.fork) is
# caught — the prior shape validated lineno only.
# Format: { rel_path: {line: primitive_name, ...} }
#
# Pass-3 TM-E2 / UM-5 design note: TM-E2 chose minimum-viable validation
# (lineno + primitive name) over the comment-anchor mechanism
# (``# AST-GATE-ALLOWLISTED: <reason>`` inline comments, the strongest
# option from the 3-option spec). Comment anchors survive re-indents but
# require non-trivial AST walker changes and a per-file comment policy.
#
# D8 deferral (see _bmad-output/implementation-artifacts/deferred-work.md):
# Implement comment anchors when EITHER (a) the spawn-site count exceeds
# 10 (currently 3 files / 4 entries) OR (b) line-number drift breaks CI
# more than once. Until then: STABILITY RULE — any edit to an allowlisted
# file MUST update the corresponding line number here in the same commit.
#
# Story 12.1 pass-1 PP13 — function-name allowlist (``_FUNC_ALLOWLIST``)
# added alongside the legacy line-keyed map. A spawn site whose enclosing
# function (or module-level scope when ``None``) matches an entry in the
# function map is accepted regardless of line drift; the legacy line map
# remains for cases where function context is ambiguous (module-level
# top-of-file calls, lambdas). New entries SHOULD prefer ``_FUNC_ALLOWLIST``
# so future edits above the spawn site (like Story 12.1's 151→175 drift)
# do not break CI.
_ALLOWLIST: dict[str, dict[int, str]] = {
    # worker-wrapper: spawns Claude Code subprocess.
    # Story 9.6 — propagates WORKER_TRACE_ID through env (FR59 / PH0).
    _rel("services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py"): {
        # Line shifted from 151 → 175 → 187 → 203 → 269 → 275 → 308 → 284
        # (G-SEC-2 env-allowlist + health_check addition + Story 49.2 base class extract).
        # PP13 — function-keyed entry in _FUNC_ALLOWLIST below is preferred;
        # this line entry is retained as defence-in-depth.
        284: "asyncio.create_subprocess_exec",
    },
    # orchestrator-adapter: spawns OMC node subprocess.
    # Story 9.6 — propagates OMB_TRACE_ID through env (FR59 / TH3).
    _rel("services/orchestrator-adapter/src/orchestrator_adapter/adapters/omc_runner.py"): {
        # Line shifted 93 → 167 (G-SEC-2 D4) → 172 (G-SEC-2 env-allowlist
        # rename/compact shifts downstream lines).
        # PP13 — function-keyed entry preferred.
        172: "asyncio.create_subprocess_exec",
    },
    # sync_upstream.py: dev-only maintenance script — clones upstream repos
    # into scripts/upstream/ for vendored-source tracking. Not invoked at
    # runtime, has no trace_id context (operator-local one-shot tool).
    # Story 9.7 / AC12 PH-B7/B8/E5: scripts/ added to _SCAN_ROOTS.
    _rel("scripts/sync_upstream.py"): {
        76: "subprocess.run",
        93: "subprocess.run",
    },
}


# PP13 — function-keyed allowlist. Maps ``rel_path`` → mapping of
# ``enclosing_func_name`` → expected primitive name. ``enclosing_func_name``
# is the name of the function (or method) whose body contains the call
# node, as captured by ``_SpawnVisitor._func_stack``. Module-level calls
# (no enclosing function) use the special key ``"<module>"``. Matches
# survive line drift caused by edits ABOVE the spawn site.
_FUNC_ALLOWLIST: dict[str, dict[str, str]] = {
    # PP35 — entries now use qualified ``Class.method`` keys instead of bare
    # method names. Distinguishes ``MockRunner._spawn`` (if ever introduced)
    # from the real production method on ``ClaudeCodeRunner._spawn``.
    # Story 49.2 — health_check moved to BaseRunner; _spawn stays in subclasses.
    _rel("services/worker-wrapper/src/worker_wrapper/adapters/base_runner.py"): {
        # Story 49.2 — shared health check probes ``<binary> --version``.
        # Short-lived, no user input, no credentials in argv/env.
        "BaseRunner.health_check": "asyncio.create_subprocess_exec",
    },
    _rel("services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py"): {
        "ClaudeCodeRunner._spawn": "asyncio.create_subprocess_exec",
    },
    _rel("services/worker-wrapper/src/worker_wrapper/adapters/codex_runner.py"): {
        # Phase 5 / Epic 26 — Codex runtime adapter. Same sandboxing as Claude.
        "CodexRunner._spawn": "asyncio.create_subprocess_exec",
    },
    _rel("services/worker-wrapper/src/worker_wrapper/adapters/gemini_runner.py"): {
        # Phase 6 / Epic 33 — Gemini runtime adapter. Same sandboxing as Claude.
        "GeminiRunner._spawn": "asyncio.create_subprocess_exec",
        # Gemini overrides health_check to add nullable-command guard; the
        # actual subprocess spawn lives in BaseRunner.health_check (see above).
    },
    _rel("services/worker-wrapper/src/worker_wrapper/app/main.py"): {
        # Worker approval flow — ``git diff --stat`` for diff summaries.
        # cwd pinned to worktree, no user input in argv, no secrets in env.
        "_get_diff_summary": "asyncio.create_subprocess_exec",
        # Story 46.1 / FR10 — PR draft creation resolves current branch via
        # ``git rev-parse --abbrev-ref HEAD`` in worktree. Short-lived, cwd
        # pinned to worktree, no user input, no secrets in argv/env.
        "_gated_action": "asyncio.create_subprocess_exec",
    },
    _rel("services/orchestrator-adapter/src/orchestrator_adapter/adapters/omc_runner.py"): {
        "OMCRunner._spawn": "asyncio.create_subprocess_exec",
    },
    _rel("mcp-servers/git/src/git_mcp/server.py"): {
        # Epic 15 / Story 15.3 — the SINGLE sandboxed ``git`` spawn-site behind
        # the Tier-1 read tools. ``create_subprocess_exec`` (never ``_shell``);
        # argv is a discrete list with ``--`` before any path filter; location
        # pinned by ``-C <root>`` + ``cwd``; env is the hermetic allowlist
        # (``_build_git_env`` — no secrets, no ``HOME``, ``GIT_CONFIG_*=/dev/null``).
        # Reads do not propagate trace_id into the git env by design (no
        # credentials enter the read path); the caller_trace_id is validated and
        # logged at the handler boundary, not threaded to git.
        "GitExecutor.run_git": "asyncio.create_subprocess_exec",
    },
    _rel("mcp-servers/verification/src/verification_mcp/server.py"): {
        # Epic 17 / Story 17.3 — the SINGLE sandboxed recipe spawn-site behind
        # the Tier-2 verification tools. ``create_subprocess_exec`` (never
        # ``_shell``); command is a discrete argv list; ``cwd`` pinned to the
        # worktree root; env is the explicit ``_ENV_ALLOWLIST``-filtered dict
        # (no secrets, no ``os.environ.copy``). Wall-clock timeout kills+reaps a
        # wedged recipe. caller_trace_id validated at the handler boundary.
        "VerificationExecutor.run_recipe": "asyncio.create_subprocess_exec",
    },
    _rel("mcp-servers/browser/src/browser_mcp/adapters/playwright_subprocess.py"): {
        # Epic 20 / Story 20-2 — the SINGLE sandboxed Docker spawn-site behind the
        # browser MCP server. ``create_subprocess_exec`` (never ``_shell``); Docker
        # argv with --memory/--cpus limits, --isolated (P4-I1), no --no-sandbox (P4-I3);
        # env is the explicit _ENV_ALLOWLIST-filtered dict (no secrets, no os.environ.copy).
        # caller_trace_id validated at the handler boundary.
        "PlaywrightSubprocessManager.spawn": "asyncio.create_subprocess_exec",
    },
    _rel("scripts/sync_upstream.py"): {
        # Both ``subprocess.run`` calls live in ``main()`` — the maintenance
        # script's single entry-point. Top-level function (no class), so no
        # qualifier prefix per PP35. PP13 — function-name accepted ALONGSIDE
        # the legacy line entries; both calls share the same enclosing scope
        # so this single key covers both.
        "main": "subprocess.run",
    },
    _rel("scripts/check_split_deployment_remote_postgres_closure.py"): {
        # Story 132.8 — static closure checker invokes documented subordinate
        # static readiness gates in-process via the current Python executable.
        # Discrete argv, cwd pinned to repo root, bounded timeout, captured
        # output, and os.environ-derived trace context is preserved across the
        # checker boundary alongside STORY_1328_CLOSURE_SUBORDINATE_GATE.
        "_run_subordinate_gate": "subprocess.run",
    },
}


# ---------------------------------------------------------------------------
# AST walker (mirrors registry-state ratchet, pass-3 TH3)
# ---------------------------------------------------------------------------


class _SpawnVisitor(ast.NodeVisitor):
    """AST walker that detects forbidden spawn primitives.

    PP13 — additionally records the enclosing function name for each
    offender so the allowlist can key on ``(line, primitive, func_name)``.
    Function-name keys are stable across edits ABOVE the spawn site
    (the historical fragility — Story 12.1 had to bump line 151→175 just
    by adding a dataclass above ``_spawn``); line-number keys remain as
    the primary anchor for ratchet compat.

    PP35 — function names are now qualified with their enclosing class
    (e.g. ``ClaudeCodeRunner._spawn``, not just ``_spawn``) so a future
    same-named method in a different class (e.g. ``MockRunner._spawn``)
    does NOT collide with the existing ``_FUNC_ALLOWLIST`` entry. Module-
    level methods keep the bare function name. Nested classes are
    dot-joined left-to-right (outer.inner.method).

    PP41 — KNOWN GAP: alias-of-alias assignments are not detected. A
    pattern such as::

        b = asyncio.create_subprocess_exec
        b(...)

    bypasses the visitor because only ``Import`` / ``ImportFrom``
    statements feed ``_import_aliases``. Adding ``visit_Assign`` for
    ``Name = Attribute`` aliasing is the next ratchet step; documented
    here so reviewers don't miss the gap. Real code review must call out
    any module-level alias rebinding of a forbidden primitive.
    """

    def __init__(self) -> None:
        self._import_aliases: dict[str, str] = {}
        # PP13 — list of (line, primitive_name, enclosing_func_name | None).
        # PP35 — enclosing_func_name is now Class.method (qualified) when
        # the spawn lives inside a class body.
        self.offenders: list[tuple[int, str, str | None]] = []
        self._func_stack: list[str] = []
        self._class_stack: list[str] = []

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

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # PP35 — track class nesting so a method's qualified name is
        # ``Class.method`` rather than just ``method``. Distinguishes
        # ``MockRunner._spawn`` from ``ClaudeCodeRunner._spawn``.
        self._class_stack.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self._class_stack.pop()

    def _qualify(self, name: str) -> str:
        """Return ``Class.name`` (or ``Outer.Inner.name``) when inside a class; else ``name``."""
        if self._class_stack:
            return ".".join([*self._class_stack, name])
        return name

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._func_stack.append(self._qualify(node.name))
        try:
            self.generic_visit(node)
        finally:
            self._func_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._func_stack.append(self._qualify(node.name))
        try:
            self.generic_visit(node)
        finally:
            self._func_stack.pop()

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
            enclosing = self._func_stack[-1] if self._func_stack else None
            self.offenders.append((node.lineno, name, enclosing))
        self.generic_visit(node)


def _scan_file(path: Path) -> list[tuple[int, str, str | None]]:
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

    def _check(rel: str, hits: list[tuple[int, str, str | None]]) -> None:
        """Apply both line- and function-keyed allowlists; collect offenders."""
        line_allowed: dict[int, str] = _ALLOWLIST.get(rel, {})
        func_allowed: dict[str, str] = _FUNC_ALLOWLIST.get(rel, {})
        for lineno, spawn_name, enclosing in hits:
            # PP13 — function-keyed path accepts the entry regardless of line
            # drift. Use the literal sentinel "<module>" for module-level calls
            # so the key is explicit + non-None.
            func_key = enclosing if enclosing is not None else "<module>"
            if func_key in func_allowed:
                if func_allowed[func_key] == spawn_name:
                    continue
                undocumented.append(
                    f"{rel}:{lineno} -> {spawn_name} (in {func_key})  "
                    f"[primitive name mismatch in func allowlist; "
                    f"expects {func_allowed[func_key]!r}]"
                )
                continue
            # Fall back to line-keyed allowlist for legacy entries.
            if lineno in line_allowed:
                if line_allowed[lineno] == spawn_name:
                    continue
                undocumented.append(
                    f"{rel}:{lineno} -> {spawn_name}  "
                    f"[primitive name mismatch; allowlist expects "
                    f"{line_allowed[lineno]!r} at this line; refactor drift?]"
                )
                continue
            undocumented.append(
                f"{rel}:{lineno} -> {spawn_name} (in {func_key})  "
                f"[not in allowlist; add to _ALLOWLIST or _FUNC_ALLOWLIST "
                f"or remove the spawn]"
            )

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
            _check(rel, hits)

    # Also scan root-level *.py files (PH-B8: top-level scripts)
    for py in sorted(_REPO_ROOT.glob("*.py")):
        if "__pycache__" in str(py) or _is_test_file(py):
            continue
        rel = str(py.relative_to(_REPO_ROOT).as_posix())
        hits = _scan_file(py)
        if not hits:
            continue
        _check(rel, hits)

    assert not undocumented, (
        "Undocumented spawn sites found — add to _ALLOWLIST or "
        "_FUNC_ALLOWLIST in tests/test_no_undocumented_spawn_sites.py and "
        "propagate trace_id through the subprocess env (Epic 9 / FR59):\n"
        + "\n".join(f"  {s}" for s in undocumented)
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
    assert any(name == "subprocess.Popen" for _, name, _enc in v.offenders)


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
    assert any(name == "asyncio.create_subprocess_exec" for _, name, _enc in v.offenders)


def test_ast_walker_captures_enclosing_func_name() -> None:
    """PP13 — _SpawnVisitor records the enclosing function name."""
    fixture = textwrap.dedent(
        """
        import asyncio

        async def _spawn():
            await asyncio.create_subprocess_exec("ls")

        async def _other():
            pass
        """
    )
    tree = ast.parse(fixture)
    v = _SpawnVisitor()
    v.visit(tree)
    matches = [
        (name, enc) for _line, name, enc in v.offenders if name == "asyncio.create_subprocess_exec"
    ]
    # PP35 — module-level functions keep their bare name (no class qualifier).
    assert matches == [("asyncio.create_subprocess_exec", "_spawn")]


def test_ast_walker_qualifies_class_method_names() -> None:
    """PP35 — _SpawnVisitor qualifies methods with their enclosing class.

    Two classes with the same method name MUST yield distinct qualified
    keys so :data:`_FUNC_ALLOWLIST` entries can target one without
    accidentally allowing the other.
    """
    fixture = textwrap.dedent(
        """
        import asyncio

        class ClaudeCodeRunner:
            async def _spawn(self):
                await asyncio.create_subprocess_exec("claude")

        class MockRunner:
            async def _spawn(self):
                await asyncio.create_subprocess_exec("mock")
        """
    )
    tree = ast.parse(fixture)
    v = _SpawnVisitor()
    v.visit(tree)
    enclosing = sorted(
        enc for _line, name, enc in v.offenders if name == "asyncio.create_subprocess_exec"
    )
    assert enclosing == ["ClaudeCodeRunner._spawn", "MockRunner._spawn"], (
        f"PP35: expected qualified Class.method names; got {enclosing!r}"
    )


def test_ast_walker_module_scope_offender_has_none_enclosing() -> None:
    """PP13 — top-level spawn (no enclosing function) → enclosing is None."""
    fixture = textwrap.dedent(
        """
        import subprocess
        subprocess.run(["ls"])
        """
    )
    tree = ast.parse(fixture)
    v = _SpawnVisitor()
    v.visit(tree)
    matches = [enc for _line, name, enc in v.offenders if name == "subprocess.run"]
    assert matches == [None]


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
    """Story 9.7 pass-2 TM-E2 / pass-3 UH-6: validate every allowlisted
    ``(path, line, primitive_name)`` triple is current.

    The ``(path, line_number)`` allowlist shape was fragile — any re-indent
    silently shifts line numbers and a non-spawn line can occupy the
    formerly-flagged number, masking a regression. Pass-3 UH-6 tightened
    the shape further: the allowlist value is now ``dict[int, str]``
    mapping line → expected primitive name. A refactor that swaps one
    spawn primitive for another at the same line (e.g. Popen → os.fork)
    is caught by the name check, not just the line check.

    Asserts both:
      * the allowlisted line actually contains a spawn call (no stale lines)
      * the spawn primitive at that line matches the allowlist's recorded
        name (refactor that swaps primitives at the same line is surfaced)
    """
    stale: list[str] = []
    for rel, lines_map in _ALLOWLIST.items():
        path = _REPO_ROOT / rel
        if not path.is_file():
            continue
        scanned: dict[int, str] = {lineno: name for lineno, name, _enc in _scan_file(path)}
        for line, expected_name in lines_map.items():
            if line not in scanned:
                stale.append(
                    f"{rel}:{line} — allowlisted but no spawn call on that line "
                    "(refactor drift; update _ALLOWLIST)"
                )
            elif scanned[line] != expected_name:
                stale.append(
                    f"{rel}:{line} — expected primitive {expected_name!r} but found "
                    f"{scanned[line]!r} (refactor swap; update _ALLOWLIST or revert swap)"
                )
    assert not stale, "Stale allowlist entries:\n" + "\n".join(f"  {s}" for s in stale)


def test_func_allowlist_entries_match_real_spawn_sites() -> None:
    """PP13 — every entry in _FUNC_ALLOWLIST must correspond to a real spawn
    call in the named function. Catches stale function-key entries after
    refactors that rename or remove the enclosing function.
    """
    stale: list[str] = []
    for rel, func_map in _FUNC_ALLOWLIST.items():
        path = _REPO_ROOT / rel
        if not path.is_file():
            stale.append(f"{rel} — file missing entirely")
            continue
        scanned_funcs: dict[str, set[str]] = {}
        for _line, primitive_name, enclosing in _scan_file(path):
            key = enclosing if enclosing is not None else "<module>"
            scanned_funcs.setdefault(key, set()).add(primitive_name)
        for func_key, expected_name in func_map.items():
            present = scanned_funcs.get(func_key, set())
            if expected_name not in present:
                stale.append(
                    f"{rel} :: {func_key} — no spawn call to "
                    f"{expected_name!r} in this function "
                    f"(found: {sorted(present)!r}); update _FUNC_ALLOWLIST"
                )
    assert not stale, "Stale func allowlist entries:\n" + "\n".join(f"  {s}" for s in stale)


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
    assert any(name == "subprocess.run" for _, name, _enc in v.offenders), (
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
    assert any(name == "subprocess.Popen" for _, name, _enc in hits)
