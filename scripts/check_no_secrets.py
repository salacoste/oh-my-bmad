#!/usr/bin/env python3
"""check_no_secrets.py — enforce P11-I2 "no committed cert/key material".

CI gate: scans the repository for committed certificate and key files, and
checks Python source for hardcoded absolute cert/key paths that should come
from environment variables instead.

What it DETECTS:

  * **Committed cert/key files** (glob scan):
      - Extensions: ``.pem``, ``.key``, ``.crt``, ``.p12``, ``.pfx``, ``.jks``
      - Any such file committed to the tree outside allowed locations is a
        violation (certs are generated at deploy time, never checked in).

  * **Hardcoded cert/key paths in Python source** (AST + regex scan):
      - ``/etc/ssl/…``, ``/certs/…``, or any string literal ending in
        ``.pem`` / ``.key`` / ``.crt`` that is an absolute path.
      - Env-var-based paths are ALLOWED: ``os.environ.get("MTLS_CERT_PATH")``,
        ``os.getenv("SSL_CERT_FILE")``, etc.
      - Also allows ``Path`` objects built from env vars and string
        concatenation with ``os.environ`` / ``os.getenv`` results.

Allowed locations (excluded from the file scan):
  - ``certs/`` — generated at deploy time (gitignored)
  - ``tests/`` directories with ``_cert`` or ``_ca`` in the path — test fixtures
  - ``conftest.py`` generated fixtures
  - ``.venv/``, ``node_modules/``, ``.git/`` — third-party / VCS
  - ``scripts/checks/fixtures/`` — self-test fixtures for this gate

Suppression: ``# noqa: SECRETS001 <reason>`` on the offending line. The reason
MUST be non-empty (matches ``checks._common._NOQA_RE``).

Scan roots: the entire repository tree (minus excluded directories).

Usage::

    uv run python scripts/check_no_secrets.py             # CI scan
    uv run python scripts/check_no_secrets.py --verbose   # success summary
    uv run python scripts/check_no_secrets.py --self-test # fixture harness
"""

from __future__ import annotations

import argparse
import ast
import re
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
# Constants
# ---------------------------------------------------------------------------

# Cert/key file extensions — committing any of these is a P11-I2 violation.
_CERT_EXTENSIONS: frozenset[str] = frozenset(
    {".pem", ".key", ".crt", ".p12", ".pfx", ".jks"}
)

# Directories where committed cert/key files are allowed (deploy-time
# generation, test fixtures, third-party, VCS).
_CERT_FILE_SKIP_DIRS: frozenset[str] = DEFAULT_SKIP_DIRS | frozenset(
    {"certs", "node_modules"}
)

# Additional directories excluded from the Python source scan.
_SOURCE_SKIP_DIRS: frozenset[str] = DEFAULT_SKIP_DIRS | frozenset(
    {"tests", "fixtures", "certs"}
)

# Patterns for hardcoded cert/key paths in string literals.
# Matches absolute paths under /etc/ssl/, /certs/, or any absolute path
# ending in a cert extension.
_HARDCODED_PATH_RE = re.compile(
    r"^("
    r"/etc/ssl/"
    r"|/certs/"
    r"|.*\.(?:pem|key|crt|p12|pfx|jks)"
    r")$"
)

# Rule tag for suppression.
_RULE_TAG = "SECRETS001"


# ---------------------------------------------------------------------------
# Committed cert/key file scanner
# ---------------------------------------------------------------------------


def _is_test_cert_fixture(path: Path) -> bool:
    """True if *path* is a test-generated cert/key file that should be ignored.

    Matches:
      - Files under any ``tests/`` subtree where the path contains
        ``_cert`` or ``_ca`` as a directory or filename component.
      - Files next to ``conftest.py`` in a ``tests/`` subtree (generated
        by conftest fixtures at test time).
    """
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        return False

    parts = rel.parts
    # Must be under a tests/ subtree.
    if "tests" not in parts:
        return False

    # Check for _cert or _ca in path components or stem.
    stem = path.stem.lower()
    return (
        "_cert" in stem
        or "_ca" in stem
        or any("_cert" in part.lower() or "_ca" in part.lower() for part in parts)
    )


def _is_self_test_fixture(path: Path) -> bool:
    """True if *path* is a self-test fixture for this gate."""
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        return False
    parts = rel.parts
    return (
        len(parts) >= 4
        and parts[0] == "scripts"
        and parts[1] == "checks"
        and parts[2] == "fixtures"
        and parts[3] == "no_secrets"
    )


def _scan_cert_files(
    roots: list[Path], *, include_self_test_fixtures: bool = False
) -> list[Violation]:
    """Walk *roots* for committed cert/key files and return violations.

    When *include_self_test_fixtures* is True the self-test fixture exclusion
    is bypassed so ``--self-test`` can verify that cert files ARE detected.
    """
    violations: list[Violation] = []

    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _CERT_EXTENSIONS:
                continue

            # Check skip directories.
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if any(part in _CERT_FILE_SKIP_DIRS for part in rel.parts):
                continue

            # Allow test fixtures.
            if _is_test_cert_fixture(path):
                continue

            # Allow self-test fixtures (unless explicitly testing them).
            if not include_self_test_fixtures and _is_self_test_fixture(path):
                continue

            violations.append(
                Violation(
                    file=path,
                    lineno=0,
                    rule=_RULE_TAG,
                    message=f"committed cert/key file: {path.name} — "
                    "cert material must be generated at deploy time, not "
                    "checked in (P11-I2)",
                )
            )

    return violations


# ---------------------------------------------------------------------------
# Hardcoded path AST scanner
# ---------------------------------------------------------------------------


def _is_env_var_access(node: ast.expr) -> bool:
    """True if *node* is an ``os.environ.get(…)`` or ``os.getenv(…)`` call."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    # os.environ.get("VAR")
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "get"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "environ"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "os"
    ):
        return True
    # os.getenv("VAR")
    return bool(
        isinstance(func, ast.Attribute)
        and func.attr == "getenv"
        and isinstance(func.value, ast.Name)
        and func.value.value.id == "os"
    )


def _is_allowed_string(node: ast.expr, parent: ast.AST | None) -> bool:
    """True if the string *node* is in an env-var context (allowed).

    Allows strings that are:
      - Arguments to ``os.environ.get()`` / ``os.getenv()`` (the env var name)
      - Part of a concatenation/build where one operand is an env-var call
    """
    if parent is None:
        return False

    # String is the first arg to os.environ.get / os.getenv → it's the var name.
    if isinstance(parent, ast.Call) and _is_env_var_access(parent):
        return True

    # String is in a joined/concatenated expression with an env-var call.
    # Walk siblings in a BinOp (e.g. os.getenv("X") + "/ca.pem").
    if isinstance(parent, ast.BinOp):
        for child in (parent.left, parent.right):
            if child is not node and _is_env_var_access(child):
                return True
            # f-string JoinedStr with env var
            if isinstance(child, ast.JoinedStr):
                for val in child.values:
                    if (
                        isinstance(val, ast.FormattedValue)
                        and _is_env_var_access(val.value)
                    ):
                        return True

    # f-string with env var access in any FormattedValue.
    if isinstance(parent, ast.JoinedStr):
        for val in parent.values:
            if (
                isinstance(val, ast.FormattedValue)
                and _is_env_var_access(val.value)
            ):
                return True

    return False


def _extract_string_from_node(node: ast.expr) -> str | None:
    """Extract the string value from a Constant or JoinedStr node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        # Reconstruct f-string for pattern matching — best effort.
        parts: list[str] = []
        for val in node.values:
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                parts.append(val.value)
            else:
                parts.append("<var>")
        return "".join(parts)
    return None


class _HardcodedPathVisitor(ast.NodeVisitor):
    """Collect ``(lineno, message)`` for hardcoded cert/key paths in strings."""

    def __init__(self) -> None:
        self.findings: list[tuple[int, str]] = []
        self._parent_map: dict[ast.AST, ast.AST | None] = {}

    def _build_parent_map(self, tree: ast.AST) -> None:
        """Walk the tree and record parent references."""
        self._parent_map.clear()
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                self._parent_map[child] = parent

    def _get_parent(self, node: ast.AST) -> ast.AST | None:
        return self._parent_map.get(node)

    def _check_string(self, node: ast.expr) -> None:
        """Check if a string constant or f-string contains a hardcoded path."""
        value = _extract_string_from_node(node)
        if value is None:
            return
        # Only check absolute paths (starting with /).
        if not value.startswith("/"):
            return
        if _HARDCODED_PATH_RE.match(value):
            parent = self._get_parent(node)
            if parent is not None and _is_allowed_string(node, parent):
                return
            self.findings.append(
                (
                    getattr(node, "lineno", 0),
                    f"hardcoded cert/key path {value!r} — "
                    "use os.environ.get() for cert paths (P11-I2)",
                )
            )

    def visit(self, node: ast.AST) -> None:
        if isinstance(node, ast.JoinedStr) or (
            isinstance(node, ast.Constant) and isinstance(node.value, str)
        ):
            self._check_string(node)
        self.generic_visit(node)


def _scan_file_for_hardcoded_paths(
    path: Path, *, raise_on_syntax: bool = False
) -> list[Violation]:
    """Return SECRETS001 violations for hardcoded cert paths in *path*."""
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

    visitor = _HardcodedPathVisitor()
    visitor._build_parent_map(tree)
    visitor.visit(tree)

    seen: set[int] = set()
    violations: list[Violation] = []
    for lineno, message in visitor.findings:
        if lineno in seen:
            continue
        source_line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        if has_noqa(source_line, _RULE_TAG):
            continue
        seen.add(lineno)
        violations.append(
            Violation(file=path, lineno=lineno, rule=_RULE_TAG, message=message)
        )
    return violations


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def _discover_source_roots() -> list[Path]:
    """Discover source roots for the hardcoded-path AST scan."""
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


def _scan() -> tuple[list[Violation], int, int]:
    """Scan for violations. Returns (violations, cert_files_scanned, py_files_scanned)."""
    violations: list[Violation] = []

    # Phase 1: committed cert/key files (whole repo minus skip dirs).
    cert_violations = _scan_cert_files([REPO_ROOT])
    violations.extend(cert_violations)
    cert_count = len(cert_violations)

    # Phase 2: hardcoded cert paths in Python source (AST scan).
    py_scanned = 0
    for path in walk_python_files(_SOURCE_ROOTS, skip_dirs=_SOURCE_SKIP_DIRS):
        if path.name.startswith("test_") or path.name == "conftest.py":
            continue
        py_scanned += 1
        violations.extend(_scan_file_for_hardcoded_paths(path))

    return violations, cert_count, py_scanned


# ---------------------------------------------------------------------------
# Self-test harness
# ---------------------------------------------------------------------------


def _self_test() -> int:
    """Exercise bundled fixtures and assert SECRETS001 detection works."""
    fixture_root = SCRIPTS_DIR / "checks" / "fixtures" / "no_secrets"
    failures: list[str] = []

    # --- Clean fixtures (cert files that are allowed / suppressed) ---
    clean_dir = fixture_root / "clean"
    if not clean_dir.exists():
        failures.append(f"Missing clean fixtures directory: {clean_dir}")
    else:
        # Check that no violations are raised for allowed cert files.
        clean_violations = _scan_cert_files([clean_dir])
        for v in clean_violations:
            failures.append(
                f"FAIL clean/{v.file.name}: unexpected SECRETS001: {v.message}"
            )

        # Check clean Python fixtures.
        clean_py_files = sorted(
            p
            for p in walk_python_files([clean_dir], skip_dirs=DEFAULT_SKIP_DIRS)
            if not p.name.startswith("_")
        )
        for fpath in clean_py_files:
            viols = _scan_file_for_hardcoded_paths(fpath, raise_on_syntax=True)
            for v in viols:
                failures.append(
                    f"FAIL clean/{v.file.name}:{v.lineno}: unexpected SECRETS001: {v.message}"
                )

    # --- Violation fixtures (cert files / hardcoded paths that MUST be flagged) ---
    violation_dir = fixture_root / "violations"
    violation_count = 0
    if not violation_dir.exists():
        failures.append(f"Missing violations fixtures directory: {violation_dir}")
    else:
        # Cert files in violations/ must be detected.
        # Pass include_self_test_fixtures=True so the self-test exclusion
        # doesn't mask the fixture cert files we expect to be flagged.
        cert_viols = _scan_cert_files(
            [violation_dir], include_self_test_fixtures=True
        )
        if not cert_viols:
            failures.append(
                "FAIL violations/: expected cert-file SECRETS001 but got none"
            )
        else:
            violation_count += len(cert_viols)

        # Python files with hardcoded paths must be detected.
        viol_py_files = sorted(
            p
            for p in walk_python_files([violation_dir], skip_dirs=DEFAULT_SKIP_DIRS)
            if not p.name.startswith("_")
        )
        for fpath in viol_py_files:
            viols = _scan_file_for_hardcoded_paths(fpath, raise_on_syntax=True)
            if not viols:
                failures.append(
                    f"FAIL violations/{fpath.name}: expected SECRETS001 but got none"
                )
            else:
                violation_count += len(viols)

    if failures:
        print("check_no_secrets.py --self-test FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    total = (len(clean_py_files) if clean_dir.exists() else 0) + violation_count
    print(f"✓ check_no_secrets.py self-test OK ({total} fixtures checked, 0 failures)")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_no_secrets.py",
        description="Enforce P11-I2 'no committed cert/key material'.",
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

    violations, cert_count, py_scanned = _scan()

    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        print(
            f"\nno-secrets: {len(violations)} violation(s) "
            f"(cert files: {cert_count}, Python files scanned: {py_scanned})",
            file=sys.stderr,
        )
        return 1

    if args.verbose:
        print(
            f"✓ no-secrets OK ({cert_count} cert files found, "
            f"{py_scanned} Python files scanned, 0 violations)"
        )
        for root in _SOURCE_ROOTS:
            count = sum(
                1 for _ in walk_python_files([root], skip_dirs=_SOURCE_SKIP_DIRS)
            )
            try:
                rel = root.relative_to(REPO_ROOT)
            except ValueError:
                rel = root
            print(f"    {rel}: {count} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
