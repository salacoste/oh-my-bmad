#!/usr/bin/env python3
"""check_mcp_transport.py — enforce Phase-2 invariant P2-I4 "MCP transport stdio-only".

CI gate: walks the first-party source trees and rejects any ``import`` /
``from-import`` of the forbidden non-stdio MCP transport modules/names.

Phase 2 baseline (still enforced):
  SSE transport is PERMANENTLY FORBIDDEN everywhere — ``mcp.server.sse``,
  ``SseServerTransport``, ``sse_app`` are rejected unconditionally.

Phase 10 (ADR-0022) streamable-http exception:
  Streamable-HTTP transport is allowed ONLY in designated files that are
  authorised to mount or consume the HTTP transport (server entry points,
  auth middleware, and client-side transport adapters). Outside these files
  ``mcp.server.streamable_http`` / ``streamable_http_app`` remain forbidden.

What it FORBIDS (AST-scan, not naive grep, so comments/strings never
false-positive):

  * **SSE — always forbidden everywhere**:
      - ``mcp.server.sse`` submodule (any import form)
      - ``SseServerTransport`` name (regardless of import source)
      - ``sse_app`` name (regardless of import source)

  * **Streamable-HTTP — forbidden OUTSIDE designated files**:
      - ``mcp.server.streamable_http`` / ``mcp.server.streamable*`` submodule
      - ``streamable_http_app`` name
      - ``mcp.client.streamable_http`` / ``streamable_http_client`` (client
        side, only in designated client files)

  Designated files where streamable-HTTP is allowed:
      - ``mcp-servers/*/src/*/__main__.py``  — server entry points
      - ``mcp-servers/*/src/*/auth/*.py``    — auth middleware modules
      - ``packages/mcp_auth/``               — shared auth middleware package
      - ``services/worker-wrapper/…/mcp_clients.py``
      - ``services/orchestrator-adapter/…/mcp_clients.py``

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

# SSE transport — PERMANENTLY FORBIDDEN everywhere. No file is ever allowed
# to import or reference SSE transport (P2-I4).
_SSE_SERVER_SUBMODULE: str = "sse"

# SSE transport names — flagged no matter which module they come from so a
# re-export shim cannot bypass detection.
_SSE_TRANSPORT_NAMES: frozenset[str] = frozenset({"SseServerTransport", "sse_app"})

# Streamable-HTTP transport — forbidden OUTSIDE designated files (ADR-0022).
# The server-side submodule prefix covers ``mcp.server.streamable_http`` and
# any future ``mcp.server.streamable*`` rename.
_STREAMABLE_HTTP_SERVER_PREFIX: str = "streamable"
_STREAMABLE_HTTP_TRANSPORT_NAMES: frozenset[str] = frozenset({"streamable_http_app"})

# Client-side streamable-HTTP module and name.
_STREAMABLE_HTTP_CLIENT_MODULE: str = "mcp.client.streamable_http"
_STREAMABLE_HTTP_CLIENT_NAME: str = "streamable_http_client"

# Convenience unions — used by the visitor for detection.
_ALL_FORBIDDEN_NAMES: frozenset[str] = _SSE_TRANSPORT_NAMES | _STREAMABLE_HTTP_TRANSPORT_NAMES


def _is_sse_server_submodule(submodule: str) -> bool:
    """True iff *submodule* is the SSE transport (``sse``)."""
    return submodule == _SSE_SERVER_SUBMODULE


def _is_streamable_http_server_submodule(submodule: str) -> bool:
    """True iff *submodule* starts with the streamable-HTTP prefix."""
    return submodule.startswith(_STREAMABLE_HTTP_SERVER_PREFIX)


def _is_forbidden_server_submodule(submodule: str) -> bool:
    """True iff *submodule* (the part after ``mcp.server.``) is any forbidden transport."""
    return _is_sse_server_submodule(submodule) or _is_streamable_http_server_submodule(submodule)


def _is_sse_module_path(module: str) -> bool:
    """True iff *module* is ``mcp.server.sse`` or a descendant."""
    parts = module.split(".")
    return (
        len(parts) >= 3
        and parts[0] == "mcp"
        and parts[1] == "server"
        and _is_sse_server_submodule(parts[2])
    )


def _is_streamable_http_module_path(module: str) -> bool:
    """True iff *module* is ``mcp.server.streamable*`` or ``mcp.client.streamable_http``."""
    parts = module.split(".")
    if len(parts) >= 3 and parts[0] == "mcp" and parts[1] == "server":
        return _is_streamable_http_server_submodule(parts[2])
    return module == _STREAMABLE_HTTP_CLIENT_MODULE or module.startswith(_STREAMABLE_HTTP_CLIENT_MODULE + ".")


def _is_forbidden_module_path(module: str) -> bool:
    """True iff a dotted *module* path is (or descends into) a forbidden transport."""
    return _is_sse_module_path(module) or _is_streamable_http_module_path(module)


def _is_sse_name(name: str) -> bool:
    """True iff *name* is an SSE transport name."""
    return name in _SSE_TRANSPORT_NAMES


def _is_streamable_http_name(name: str) -> bool:
    """True iff *name* is a streamable-HTTP transport name."""
    return name in _STREAMABLE_HTTP_TRANSPORT_NAMES or name == _STREAMABLE_HTTP_CLIENT_NAME


# ---------------------------------------------------------------------------
# Streamable-HTTP allowlist (ADR-0022 Phase 10)
# ---------------------------------------------------------------------------


def _is_streamable_http_allowed_file(path: Path) -> bool:
    """True iff *path* is a designated file where streamable-HTTP imports are allowed.

    Allowed patterns:
      - ``mcp-servers/*/src/*/__main__.py``  — server entry points
      - ``mcp-servers/*/src/*/auth/*.py``    — auth middleware modules
      - ``packages/mcp_auth/**``             — shared auth middleware package
      - ``services/worker-wrapper/…/mcp_clients.py``
      - ``services/orchestrator-adapter/…/mcp_clients.py``
    """
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        return False

    parts = rel.parts

    # packages/mcp_auth/**  (packages/mcp_auth/src/... or packages/mcp_auth/...)
    if len(parts) >= 2 and parts[0] == "packages" and parts[1] == "mcp_auth":
        return True

    # mcp-servers/*/src/*/__main__.py  — server entry points
    if (
        len(parts) >= 5
        and parts[0] == "mcp-servers"
        and parts[2] == "src"
        and parts[4] == "__main__.py"
    ):
        return True

    # mcp-servers/*/src/*/auth/*.py  — auth middleware modules
    if (
        len(parts) >= 6
        and parts[0] == "mcp-servers"
        and parts[2] == "src"
        and parts[4] == "auth"
        and parts[5].endswith(".py")
    ):
        return True

    # services/worker-wrapper/…/mcp_clients.py
    if (
        len(parts) >= 2
        and parts[0] == "services"
        and parts[1] == "worker-wrapper"
        and path.name == "mcp_clients.py"
    ):
        return True

    # services/orchestrator-adapter/…/mcp_clients.py
    if (
        len(parts) >= 2
        and parts[0] == "services"
        and parts[1] == "orchestrator-adapter"
        and path.name == "mcp_clients.py"
    ):
        return True

    # scripts/checks/fixtures/mcp_transport/** — self-test fixtures simulating
    # allowed paths.  These are excluded from the main scan (not under source
    # roots), so the allowlist only matters for ``--self-test`` which calls
    # ``_scan_file`` directly.
    return (
        len(parts) >= 4
        and parts[0] == "scripts"
        and parts[1] == "checks"
        and parts[2] == "fixtures"
        and parts[3] == "mcp_transport"
    )


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
    """Collect ``(lineno, kind, message)`` tuples for every MCP001 candidate.

    *kind* is ``"sse"`` or ``"streamable_http"`` and is used by ``_scan_file``
    to decide whether the finding should be suppressed for files on the
    streamable-HTTP allowlist.

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
        self.findings: list[tuple[int, str, str]] = []  # (lineno, kind, message)

    @staticmethod
    def _kind_for_module(module: str) -> str:
        if _is_sse_module_path(module):
            return "sse"
        return "streamable_http"

    @staticmethod
    def _kind_for_server_submodule(submodule: str) -> str:
        if _is_sse_server_submodule(submodule):
            return "sse"
        return "streamable_http"

    @staticmethod
    def _kind_for_name(name: str) -> str:
        if _is_sse_name(name):
            return "sse"
        return "streamable_http"

    # `import mcp.server.sse` / `import mcp.server.sse as transport`
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _is_forbidden_module_path(alias.name):
                kind = self._kind_for_module(alias.name)
                self.findings.append(
                    (
                        node.lineno,
                        kind,
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
                kind = self._kind_for_module(module)
                names = ", ".join(alias.name for alias in node.names) or "*"
                self.findings.append(
                    (
                        node.lineno,
                        kind,
                        f"from {module} import {names} — "
                        "non-stdio MCP transport module forbidden; "
                        "use mcp.server.stdio (P2-I4)",
                    )
                )
            elif module == "mcp.server":
                # `from mcp.server import sse` / `… import streamable_http`
                for alias in node.names:
                    if _is_forbidden_server_submodule(alias.name):
                        kind = self._kind_for_server_submodule(alias.name)
                        self.findings.append(
                            (
                                node.lineno,
                                kind,
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
            if alias.name in _ALL_FORBIDDEN_NAMES or alias.name == _STREAMABLE_HTTP_CLIENT_NAME:
                src = node.module if node.module else "."
                kind = self._kind_for_name(alias.name)
                self.findings.append(
                    (
                        node.lineno,
                        kind,
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

    streamable_allowed = _is_streamable_http_allowed_file(path)

    seen: set[int] = set()
    violations: list[Violation] = []
    for lineno, kind, message in visitor.findings:
        if lineno in seen:
            continue
        # Streamable-HTTP findings are skipped in designated files (ADR-0022).
        if kind == "streamable_http" and streamable_allowed:
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
# mTLS validation (Phase 10 — Story 58)
# ---------------------------------------------------------------------------


def _file_defines_function(tree: ast.Module, name: str) -> bool:
    """Return True iff *tree* contains a top-level ``def {name}``."""
    return any(
        isinstance(node, ast.FunctionDef) and node.name == name for node in tree.body
    )


def _file_imports_name(tree: ast.Module, module: str, name: str) -> bool:
    """Return True iff *tree* contains ``from {module} import {name}``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level != 0 or node.module != module:
            continue
        return any(alias.name == name for alias in node.names)
    return False


def _file_calls_name(tree: ast.Module, name: str) -> bool:
    """Return True iff *tree* contains a call expression whose func is *name*."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == name:
                return True
            if isinstance(func, ast.Attribute) and func.attr == name:
                return True
    return False


def _check_mtls_servers() -> list[Violation]:
    """Verify every MCP server ``__main__.py`` with ``_run_streamable_http``
    imports and calls ``create_uvicorn_ssl_config`` from ``mtls``."""
    violations: list[Violation] = []
    for main_py in sorted(REPO_ROOT.glob("mcp-servers/*/src/*/__main__.py")):
        try:
            source = main_py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(main_py))
        except SyntaxError:
            continue

        if not _file_defines_function(tree, "_run_streamable_http"):
            continue

        if not _file_imports_name(tree, "mtls", "create_uvicorn_ssl_config"):
            violations.append(
                Violation(
                    file=main_py,
                    lineno=0,
                    rule="MTLS001",
                    message=(
                        "file defines _run_streamable_http but does not import "
                        "create_uvicorn_ssl_config from mtls"
                    ),
                )
            )
            continue

        if not _file_calls_name(tree, "create_uvicorn_ssl_config"):
            violations.append(
                Violation(
                    file=main_py,
                    lineno=0,
                    rule="MTLS001",
                    message=(
                        "file imports create_uvicorn_ssl_config from mtls "
                        "but never calls it"
                    ),
                )
            )

    return violations


def _check_mtls_clients() -> list[Violation]:
    """Verify both ``mcp_clients.py`` files import ``create_httpx_verify_arg``
    from ``mtls``."""
    violations: list[Violation] = []
    client_files = [
        REPO_ROOT
        / "services"
        / "worker-wrapper"
        / "src"
        / "worker_wrapper"
        / "adapters"
        / "mcp_clients.py",
        REPO_ROOT
        / "services"
        / "orchestrator-adapter"
        / "src"
        / "orchestrator_adapter"
        / "adapters"
        / "mcp_clients.py",
    ]
    for client_py in client_files:
        try:
            source = client_py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            violations.append(
                Violation(
                    file=client_py,
                    lineno=0,
                    rule="MTLS001",
                    message="mcp_clients.py not found or unreadable",
                )
            )
            continue
        try:
            tree = ast.parse(source, filename=str(client_py))
        except SyntaxError:
            continue

        if not _file_imports_name(tree, "mtls", "create_httpx_verify_arg"):
            violations.append(
                Violation(
                    file=client_py,
                    lineno=0,
                    rule="MTLS001",
                    message=(
                        "mcp_clients.py does not import "
                        "create_httpx_verify_arg from mtls"
                    ),
                )
            )

    return violations


def _scan_mtls() -> list[Violation]:
    """Run all mTLS structural checks and return violations."""
    violations: list[Violation] = []
    violations.extend(_check_mtls_servers())
    violations.extend(_check_mtls_clients())
    return violations


# ---------------------------------------------------------------------------
# mTLS self-test helpers (used by --self-test for MTLS fixture harness)
# ---------------------------------------------------------------------------


def _scan_mtls_fixture(path: Path) -> list[Violation]:
    """Run the mTLS structural check on a single fixture *path*.

    Determines check type from the fixture filename prefix:
      - ``server_…`` → _check_mtls_servers pattern
      - ``client_…`` → _check_mtls_clients pattern
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    violations: list[Violation] = []
    name = path.name

    if name.startswith("server_"):
        if _file_defines_function(tree, "_run_streamable_http"):
            if not _file_imports_name(tree, "mtls", "create_uvicorn_ssl_config"):
                violations.append(
                    Violation(
                        file=path,
                        lineno=0,
                        rule="MTLS001",
                        message="missing import create_uvicorn_ssl_config from mtls",
                    )
                )
            elif not _file_calls_name(tree, "create_uvicorn_ssl_config"):
                violations.append(
                    Violation(
                        file=path,
                        lineno=0,
                        rule="MTLS001",
                        message="create_uvicorn_ssl_config imported but not called",
                    )
                )
    elif name.startswith("client_") and not _file_imports_name(tree, "mtls", "create_httpx_verify_arg"):
        violations.append(
            Violation(
                file=path,
                lineno=0,
                rule="MTLS001",
                message="missing import create_httpx_verify_arg from mtls",
            )
        )

    return violations


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

    # mTLS structural fixtures — clean/mtls/ and violations/mtls/
    mtls_fixture_root = fixture_root / "mtls"
    mtls_clean_files: list[Path] = []
    mtls_violation_files: list[Path] = []

    mtls_clean_dir = mtls_fixture_root / "clean"
    if mtls_clean_dir.exists():
        mtls_clean_files = _list_fixture_files(mtls_clean_dir)
        for fpath in mtls_clean_files:
            viols = _scan_mtls_fixture(fpath)
            for v in viols:
                failures.append(
                    f"FAIL mtls/clean/{v.file.name}: unexpected MTLS001: {v.message}"
                )

    mtls_violation_dir = mtls_fixture_root / "violations"
    if mtls_violation_dir.exists():
        mtls_violation_files = _list_fixture_files(mtls_violation_dir)
        for fpath in mtls_violation_files:
            viols = _scan_mtls_fixture(fpath)
            if not viols:
                failures.append(
                    f"FAIL mtls/violations/{fpath.name}: expected MTLS001 but got none"
                )

    if failures:
        print("check_mcp_transport.py --self-test FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    total = len(clean_files) + len(violation_files) + len(mtls_clean_files) + len(mtls_violation_files)
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

    transport_violations, scanned = _scan(_SOURCE_ROOTS)
    mtls_violations = _scan_mtls()
    all_violations = transport_violations + mtls_violations

    if all_violations:
        for v in all_violations:
            print(v, file=sys.stderr)
        print(
            f"\nmcp-transport: {len(all_violations)} violation(s) "
            f"({scanned} files scanned, {len(mtls_violations)} mTLS structural).",
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
        print(f"    mTLS structural checks: "
              f"{len(list(REPO_ROOT.glob('mcp-servers/*/src/*/__main__.py')))} servers, "
              f"2 clients — 0 violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
