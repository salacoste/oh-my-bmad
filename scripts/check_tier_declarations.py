#!/usr/bin/env python3
"""check_tier_declarations.py — enforce Phase-3 invariant P3-I1 "every MCP tool declares a tier".

CI gate (Epic 15 / Story 15.2a): AST-walks every
``mcp-servers/**/handlers/tools.py``, collects every ``@mcp.tool()``-registered
handler, and asserts each one declares its required capability tier — i.e. its
body references ``TIER_MAP["<key>"]`` (directly, or inside a ``check_tier(...)`` /
``check_tier_with_approval(...)`` call — the subscript shows up identically in
all three forms) AND ``<key>`` is a member of the module-level ``TIER_MAP`` dict
literal in that same file. A registered tool with NO ``TIER_MAP[...]`` reference
is **untiered** (P3-I1 build-time failure); a registered tool whose tier key has
no matching ``TIER_MAP`` entry is an **orphan** (the declaration is a typo / the
dict-literal entry is missing). Both exit non-zero.

This gate **does not pre-exist** — it is the Epic-15 deliverable that turns
"declare a tier" from a convention into a structural invariant; Epics 16–19
inherit it unchanged (`architecture.md:1537`).

Why AST, not grep: the tier key is a *string subscript of ``TIER_MAP``* inside
the handler body (e.g. ``check_tier(action, ctx, TIER_MAP["task.add_note"])``),
NOT the function name, and ``@mcp.tool()`` appears in several decorator forms.
A naive grep would false-positive on docstrings/comments and miss the
key↔dict-literal cross-check.

What counts as a *tier declaration* (the hardened rule — Story 15.2a review):
a ``TIER_MAP["<key>"]`` subscript counts ONLY when it is a **direct argument**
(positional or keyword) to a ``check_tier(...)`` / ``check_tier_with_approval(...)``
call that sits **directly in the tool handler's own body** — i.e. statements of
the ``@*.tool()`` handler itself, NOT inside a nested ``def``/``lambda`` defined
within it. The audited real servers (task-registry, session-registry) all use
exactly this direct-argument form
(``check_tier("task.add_note", CallerContext(...), TIER_MAP["task.add_note"])``),
so no variable-indirection support is required. This narrowing closes two
false-negatives an earlier ``ast.walk``-based rule admitted:

  * **P1a (dead code):** a bare ``TIER_MAP["x"]`` subscript inside an unreachable
    branch (``if False:`` / ``return`` then dead block) — the gate is satisfied
    while the real op runs ungated. Now rejected: a bare subscript that is not a
    ``check_tier*`` argument no longer counts.
  * **P1b (uncalled nested helper):** the only ``TIER_MAP[...]`` living inside an
    uncalled inner ``def`` — the gate is satisfied while the handler's own body
    never gates. Now rejected: subscripts inside nested functions/lambdas are not
    scanned for the enclosing handler.

Residual limitation (out of scope — no dataflow proof): the gate confirms a
``check_tier*`` call carrying a ``TIER_MAP[...]`` argument is present in the
handler body; it does NOT prove that call is reachable or that its result is
honoured. A ``check_tier(...)`` wrapped in ``if False:`` (a check_tier call, not
a bare subscript, deliberately placed in dead code) or a call whose raised
``CapabilityDenied`` is swallowed would still pass. Proving reachability /
honouring is a dataflow problem and is intentionally not attempted here — the
gate's contract is "the handler's own body invokes the documented tier-check
with a TIER_MAP key", which is the structural invariant Epics 16–19 inherit.

What it checks per ``handlers/tools.py``:

  * Collect the module-level ``TIER_MAP`` dict-literal keys (the string
    constants in ``TIER_MAP: ... = {"<key>": Tier.X, ...}``).
  * For every function decorated with a tool decorator
    (``@mcp.tool()`` / ``@mcp.tool(...)`` / ``@<var>.tool()`` / bare
    ``@mcp.tool``), collect every ``TIER_MAP["<key>"]`` subscript that is a direct
    argument to a ``check_tier(...)`` / ``check_tier_with_approval(...)`` call in
    the handler's OWN body (nested functions/lambdas excluded).
      - zero such subscripts → TIER001 untiered tool (the P3-I1 failure)
      - a subscript key absent from the dict literal → TIER002 orphan tier key
  * A file with an empty ``TIER_MAP`` and zero tool handlers (git-mcp's current
    scaffold) passes **vacuously**.

The ``mcp`` / server variable name is whatever the local ``register_tools``
binds — the decorator is matched on the attribute name ``tool`` of any receiver,
so ``@mcp.tool()`` and ``@server.tool()`` are both recognised.

Suppression: ``# noqa: TIER001 <reason>`` / ``# noqa: TIER002 <reason>`` on the
offending line (the decorator line for TIER001, the subscript line for TIER002),
via the shared ``checks._common`` helpers. The reason MUST be non-empty.

Scan roots (first-party MCP servers, glob-discovered so a new server is covered
automatically): every ``mcp-servers/*/src/**/handlers/tools.py``. The self-test
fixture tree (``scripts/checks/fixtures/tier_declarations/``) is scanned only by
``--self-test``.

Usage::

    uv run python scripts/check_tier_declarations.py             # CI scan
    uv run python scripts/check_tier_declarations.py --verbose   # success summary
    uv run python scripts/check_tier_declarations.py --self-test # fixture harness
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
    Violation,
    has_noqa,
)

# Module-level dict whose string-literal keys enumerate the declared tiers.
_TIER_MAP_NAME = "TIER_MAP"

# The tool-registration decorator attribute. Matched on the attribute name only
# (``<receiver>.tool``) so the server-variable name is irrelevant —
# ``@mcp.tool()`` and ``@server.tool()`` both register a tool.
_TOOL_DECORATOR_ATTR = "tool"

# The documented tier-check call names. A ``TIER_MAP[...]`` subscript counts as a
# tier declaration ONLY when it is a direct argument to one of these calls.
_CHECK_TIER_FUNCS = frozenset({"check_tier", "check_tier_with_approval"})

_UNTIERED_RULE = "TIER001"
_ORPHAN_RULE = "TIER002"


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _decorator_is_tool(decorator: ast.expr) -> bool:
    """True iff *decorator* registers an MCP tool.

    Recognised forms (the receiver — ``mcp`` / ``server`` / any local var — is
    intentionally ignored; only the ``.tool`` attribute matters):

      * ``@mcp.tool()``       — Call whose func is ``<recv>.tool``
      * ``@mcp.tool(name=…)``  — Call (with args) whose func is ``<recv>.tool``
      * ``@server.tool()``     — same, different receiver name
      * ``@mcp.tool``          — bare Attribute access (defensive; FastMCP uses
                                 the call form, but a bare attr would still
                                 register, so treat it as a tool)
    """
    # @recv.tool(...)  -> Call(func=Attribute(attr="tool"))
    if isinstance(decorator, ast.Call):
        func = decorator.func
        return isinstance(func, ast.Attribute) and func.attr == _TOOL_DECORATOR_ATTR
    # @recv.tool        -> Attribute(attr="tool")
    if isinstance(decorator, ast.Attribute):
        return decorator.attr == _TOOL_DECORATOR_ATTR
    return False


def _function_is_tool(node: ast.AST) -> bool:
    """True iff *node* is a (sync/async) function carrying a tool decorator."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    return any(_decorator_is_tool(dec) for dec in node.decorator_list)


def _tier_map_subscript_key(node: ast.expr) -> str | None:
    """Return the string key of a ``TIER_MAP["<key>"]`` subscript, else None.

    Matches ``ast.Subscript`` where the value is ``Name(id="TIER_MAP")`` and the
    index is a string ``Constant``. Whether such a subscript *counts* as a tier
    declaration is decided by ``_tool_subscript_findings`` (it must be a direct
    ``check_tier*`` argument in the handler's own body); this helper only
    recognises the subscript shape and extracts its key.
    """
    if not isinstance(node, ast.Subscript):
        return None
    if not (isinstance(node.value, ast.Name) and node.value.id == _TIER_MAP_NAME):
        return None
    index = node.slice
    if isinstance(index, ast.Constant) and isinstance(index.value, str):
        return index.value
    return None


def _call_is_check_tier(node: ast.expr) -> bool:
    """True iff *node* is a ``check_tier(...)`` / ``check_tier_with_approval(...)`` call.

    The function is matched by name, both as a bare ``Name`` (the real servers'
    ``from capabilities import check_tier`` import) and as an ``Attribute`` access
    (``<mod>.check_tier(...)``) — only the final attribute name is significant.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _CHECK_TIER_FUNCS
    if isinstance(func, ast.Attribute):
        return func.attr in _CHECK_TIER_FUNCS
    return False


def _collect_tier_map_keys(tree: ast.Module) -> set[str]:
    """Return the string-literal keys of the module-level ``TIER_MAP`` dict.

    Handles the annotated assignment ``TIER_MAP: dict[str, Tier] = {...}`` (the
    repo convention) and the plain ``TIER_MAP = {...}`` form. Only top-level
    assignments are considered (the dict is a module global). Non-string and
    non-constant keys are ignored — the gate cares only about the declared
    string action keys.
    """
    keys: set[str] = set()
    for stmt in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets = [stmt.target]
            value = stmt.value
        elif isinstance(stmt, ast.Assign):
            targets = stmt.targets
            value = stmt.value
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == _TIER_MAP_NAME for t in targets):
            continue
        if isinstance(value, ast.Dict):
            for key in value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    keys.add(key.value)
    return keys


def _iter_own_body_nodes(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.AST]:
    """Yield every node in *func*'s OWN body, not descending into nested scopes.

    Like ``ast.walk`` but pruned at nested ``def`` / ``async def`` / ``lambda``
    boundaries: nodes belonging to a function defined inside the handler are NOT
    yielded. This is what makes the "uncalled nested helper" bypass (P1b)
    impossible — a ``TIER_MAP[...]`` living only inside an inner ``def`` is never
    reached. *func* itself is not yielded; only its descendants.
    """
    out: list[ast.AST] = []
    # Seed with the direct children of the handler (skip the handler node so we
    # never re-enter it as a "nested" scope).
    stack: list[ast.AST] = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        out.append(node)
        # Do not descend into a nested function/lambda scope: anything declared
        # there is not part of THIS handler's own gating body.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return out


def _tool_subscript_findings(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, int]]:
    """Return ``(tier_key, lineno)`` for each qualifying ``TIER_MAP["<key>"]`` in *func*.

    A subscript counts ONLY when it is a direct argument (positional, ``*args``
    star-arg, or keyword) to a ``check_tier(...)`` / ``check_tier_with_approval(...)``
    call located in *func*'s OWN body (nested functions/lambdas excluded). This is
    the hardened rule: a bare ``TIER_MAP[...]`` not wired into a ``check_tier*``
    call (P1a dead-code bypass) and a subscript reachable only inside an uncalled
    inner ``def`` (P1b bypass) no longer satisfy the gate.

    The audited real servers pass the subscript directly
    (``check_tier(action, ctx, TIER_MAP["x"])``); no variable-indirection
    (``tier = TIER_MAP[...]; check_tier(..., tier)``) is used, so it is
    intentionally not supported — keeping the rule as tight as the real code
    allows.
    """
    findings: list[tuple[str, int]] = []
    for node in _iter_own_body_nodes(func):
        if not _call_is_check_tier(node):
            continue
        assert isinstance(node, ast.Call)  # narrowed by _call_is_check_tier
        # Direct positional args, *star-args, and keyword-arg values.
        arg_exprs: list[ast.expr] = list(node.args)
        arg_exprs.extend(kw.value for kw in node.keywords)
        for arg in arg_exprs:
            # Unwrap a ``*TIER_MAP[...]`` star-arg to its inner subscript.
            target = arg.value if isinstance(arg, ast.Starred) else arg
            key = _tier_map_subscript_key(target)
            if key is not None:
                findings.append((key, target.lineno))
    return findings


def _tool_decorator_lineno(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Return the line of the first tool decorator (for TIER001 reporting)."""
    for dec in func.decorator_list:
        if _decorator_is_tool(dec):
            return dec.lineno
    return func.lineno


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def _scan_file(path: Path) -> list[Violation]:
    """Return tier-declaration violations found in *path*.

    A ``SyntaxError`` surfaces as an empty result (mypy --strict / ruff would
    fail first on a broken source file).
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
    declared_keys = _collect_tier_map_keys(tree)
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if not _function_is_tool(node):
            continue
        assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))  # narrow for mypy

        findings = _tool_subscript_findings(node)

        # TIER001 — registered tool that never references TIER_MAP["<key>"].
        if not findings:
            dec_lineno = _tool_decorator_lineno(node)
            source_line = lines[dec_lineno - 1] if dec_lineno <= len(lines) else ""
            if not has_noqa(source_line, _UNTIERED_RULE):
                violations.append(
                    Violation(
                        file=path,
                        lineno=dec_lineno,
                        rule=_UNTIERED_RULE,
                        message=(
                            f"@*.tool()-registered handler {node.name!r} declares no "
                            f"capability tier — its own body must pass "
                            f'TIER_MAP["<action>"] as an argument to check_tier(...) or '
                            "check_tier_with_approval(...) (a bare subscript, or one "
                            "reachable only inside a nested helper, does not count). "
                            "Every MCP tool must declare a tier (P3-I1)."
                        ),
                    )
                )
            continue

        # TIER002 — a referenced tier key with no matching TIER_MAP entry.
        for key, lineno in findings:
            if key in declared_keys:
                continue
            source_line = lines[lineno - 1] if lineno <= len(lines) else ""
            if has_noqa(source_line, _ORPHAN_RULE):
                continue
            violations.append(
                Violation(
                    file=path,
                    lineno=lineno,
                    rule=_ORPHAN_RULE,
                    message=(
                        f"handler {node.name!r} references TIER_MAP[{key!r}] but no such "
                        f"key exists in the module-level TIER_MAP dict literal — add the "
                        "entry (or fix the key). Every MCP tool must declare a tier (P3-I1)."
                    ),
                )
            )

    return violations


def _discover_tool_files(root: Path) -> list[Path]:
    """Glob-discover every ``mcp-servers/*/src/**/handlers/tools.py`` under *root*.

    Glob-based (not hand-curated) so a newly added MCP server's tool file is
    covered automatically — mirrors check_mcp_transport.py's source-root
    discovery.
    """
    parent = root / "mcp-servers"
    if not parent.exists():
        return []
    return sorted(parent.glob("*/src/**/handlers/tools.py"))


def _scan(paths: list[Path]) -> tuple[list[Violation], int]:
    """Scan *paths* and return ``(violations, files_scanned)``."""
    violations: list[Violation] = []
    scanned = 0
    for path in paths:
        if not path.is_file():
            continue
        scanned += 1
        violations.extend(_scan_file(path))
    return violations, scanned


# ---------------------------------------------------------------------------
# Self-test harness
# ---------------------------------------------------------------------------


def _list_fixture_files(directory: Path) -> list[Path]:
    """Return fixture ``tools.py`` files under *directory* (ignore ``_``-prefixed)."""
    if not directory.exists():
        return []
    return sorted(p for p in directory.rglob("*.py") if not p.name.startswith("_"))


# Fixtures whose violation MUST carry a specific rule — proving the bypass is
# caught with the right code, not merely "some" violation. Keyed by filename.
_EXPECTED_VIOLATION_RULE: dict[str, str] = {
    "untiered_tool.py": _UNTIERED_RULE,
    "orphan_tier_key.py": _ORPHAN_RULE,
    # Story 15.2a hardening — the two evasion shapes must now be TIER001:
    "dead_code_bypass.py": _UNTIERED_RULE,
    "uncalled_nested_helper.py": _UNTIERED_RULE,
}


def _self_test() -> int:
    """Exercise bundled fixtures and assert TIER001/TIER002 detection works.

    ``clean/`` fixtures must surface ZERO violations (a registered tool whose
    tier IS declared, plus a vacuously-clean empty-TIER_MAP file). ``violations/``
    fixtures must each surface at least one violation; those listed in
    ``_EXPECTED_VIOLATION_RULE`` must surface that *specific* rule — in
    particular the two Story-15.2a bypass fixtures (dead-code subscript,
    uncalled-nested-helper) must each be flagged TIER001, proving the hardened
    "must be a check_tier* argument in the handler's own body" rule closes them.
    """
    fixture_root = SCRIPTS_DIR / "checks" / "fixtures" / "tier_declarations"
    failures: list[str] = []

    clean_dir = fixture_root / "clean"
    clean_files = _list_fixture_files(clean_dir)
    if not clean_dir.exists():
        failures.append(f"Missing clean fixtures directory: {clean_dir}")
    elif not clean_files:
        failures.append(f"Empty clean fixtures directory: {clean_dir}")
    for fpath in clean_files:
        for v in _scan_file(fpath):
            failures.append(
                f"FAIL clean/{v.file.name}:{v.lineno}: unexpected {v.rule}: {v.message}"
            )

    violation_dir = fixture_root / "violations"
    violation_files = _list_fixture_files(violation_dir)
    if not violation_dir.exists():
        failures.append(f"Missing violations fixtures directory: {violation_dir}")
    elif not violation_files:
        failures.append(f"Empty violations fixtures directory: {violation_dir}")
    for fpath in violation_files:
        found = _scan_file(fpath)
        if not found:
            failures.append(
                f"FAIL violations/{fpath.name}: expected a tier-declaration violation but got none"
            )
            continue
        expected = _EXPECTED_VIOLATION_RULE.get(fpath.name)
        if expected is not None and not any(v.rule == expected for v in found):
            got = sorted({v.rule for v in found})
            failures.append(f"FAIL violations/{fpath.name}: expected {expected} but got {got}")

    if failures:
        print("check_tier_declarations.py --self-test FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    total = len(clean_files) + len(violation_files)
    print(f"✓ check_tier_declarations.py self-test OK ({total} fixtures, 0 failures)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_tier_declarations.py",
        description="Enforce Phase-3 invariant P3-I1 'every MCP tool declares a tier'.",
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

    tool_files = _discover_tool_files(REPO_ROOT)
    violations, scanned = _scan(tool_files)

    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        print(
            f"\ntier-declarations: {len(violations)} violation(s) in {scanned} "
            "handlers/tools.py file(s) scanned.",
            file=sys.stderr,
        )
        return 1

    if args.verbose:
        print(f"✓ tier-declarations OK ({scanned} handlers/tools.py files scanned, 0 violations)")
        for path in tool_files:
            try:
                rel = path.relative_to(REPO_ROOT)
            except ValueError:
                rel = path
            print(f"    {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
