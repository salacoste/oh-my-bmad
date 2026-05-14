"""Pre-commit hook entrypoint for the secret-hygiene scanner.

Invoked by pre-commit (via ``uv run secret-hygiene-precommit``) with the
list of staged file paths as positional arguments.  Exits 1 if any secret
pattern is found; 0 otherwise.

Usage (direct)::

    uv run secret-hygiene-precommit [--allowlist-file PATH] [-v] [file ...]

Usage (pre-commit wires this automatically via .pre-commit-config.yaml)::

    # .pre-commit-config.yaml
    repos:
      - repo: local
        hooks:
          - id: secret-hygiene-precommit
            entry: uv run secret-hygiene-precommit
            language: system
            types: [text]
"""

from __future__ import annotations

import argparse
import os
import sys
from fnmatch import fnmatchcase
from pathlib import Path

from .license_scan import REPO_LICENSE, scan_files_for_licenses
from .path_checks import (
    Violation,
    check_commit_message,
    check_sensitive_paths,
    check_worktree_boundary,
)
from .scanner import scan_file

# ---------------------------------------------------------------------------
# Default ignore-file name (parallels .gitignore / .dockerignore convention)
# ---------------------------------------------------------------------------

_DEFAULT_IGNORE_FILENAME = ".secret-hygiene-ignore"


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------


def _find_default_allowlist(start: Path | None = None) -> Path | None:
    """Walk upward from *start* (default cwd) to find ``.secret-hygiene-ignore``.

    Returns the first matching :class:`~pathlib.Path`, or ``None`` if no file
    is found before the filesystem root.
    """
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        path = candidate / _DEFAULT_IGNORE_FILENAME
        if path.is_file():
            return path
    return None


# ---------------------------------------------------------------------------
# Allowlist loading
# ---------------------------------------------------------------------------


def _load_allowlist(allowlist_path: str) -> list[str]:
    """Return glob patterns from *allowlist_path*, skipping comments + blanks.

    Expands ``~`` and environment variables in *allowlist_path* before opening.
    Raises :class:`SystemExit` with code 2 on :exc:`FileNotFoundError`.
    """
    expanded = os.path.expandvars(os.path.expanduser(allowlist_path))
    try:
        raw_lines = Path(expanded).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        print(
            f"error: secret-hygiene: allowlist file not found: {expanded}",
            file=sys.stderr,
        )
        sys.exit(2)
    globs: list[str] = []
    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        globs.append(line)
    return globs


# ---------------------------------------------------------------------------
# Glob matching (supports ** prefix)
# ---------------------------------------------------------------------------


def _glob_match(file_str: str, pattern: str) -> bool:
    """Return ``True`` if *file_str* matches *pattern*.

    Handles plain ``fnmatch`` patterns plus the ``**/name`` convention
    (matches the basename at any depth).
    """
    if fnmatchcase(file_str, pattern):
        return True
    # Handle **/foo or **/path/to/foo: match at any depth.
    if pattern.startswith("**/"):
        suffix = pattern[3:]
        # Match the full tail path, or any single path segment.
        if fnmatchcase(file_str, suffix):
            return True
        # Walk path segments: **/foo.py matches a/b/foo.py by checking suffix
        # against progressively shorter tails.
        parts = file_str.split("/")
        for i in range(len(parts)):
            tail = "/".join(parts[i:])
            if fnmatchcase(tail, suffix):
                return True
    return False


def _is_allowlisted(file_path: str, globs: list[str]) -> bool:
    """Return ``True`` if *file_path* matches any glob in *globs*."""
    return any(_glob_match(file_path, pattern) for pattern in globs)


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Scan files for secrets and report violations.

    Parameters
    ----------
    argv:
        Argument list WITHOUT the program name (i.e. ``sys.argv[1:]``).
        When ``None`` (the default used by the ``[project.scripts]`` entry
        point), falls back to ``sys.argv[1:]``.

    Returns
    -------
    int
        0 if no secrets found; 1 if any match is detected.
    """
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(
        prog="secret-hygiene-precommit",
        description="Scan files for secret patterns and block commits that contain them.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Files to scan (pre-commit passes staged files as positional args).",
    )
    parser.add_argument(
        "--allowlist-file",
        metavar="PATH",
        default=None,
        help=(
            "Path to a file with one glob pattern per line; matching files are skipped. "
            "When omitted, the scanner auto-discovers a .secret-hygiene-ignore file by "
            "walking upward from the current working directory."
        ),
    )
    parser.add_argument(
        "--worktree-root",
        metavar="PATH",
        default=None,
        help=(
            "Root of the assigned worktree. Files resolved outside this root "
            "are flagged as boundary violations. Defaults to cwd."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print a summary line when no secrets are found.",
    )
    parser.add_argument(
        "--repo-license",
        metavar="SPDX",
        default=REPO_LICENSE,
        help=f"Repository's declared license for compatibility checks (default: {REPO_LICENSE}).",
    )

    args = parser.parse_args(argv)

    # Load allowlist globs.
    # If --allowlist-file was given explicitly, use that path (with expanduser/expandvars).
    # Otherwise auto-discover .secret-hygiene-ignore from cwd upward.
    allowlist_globs: list[str] = []
    if args.allowlist_file is not None:
        allowlist_globs = _load_allowlist(args.allowlist_file)
    else:
        discovered = _find_default_allowlist()
        if discovered is not None:
            if args.verbose:
                print(
                    f"# auto-discovered {_DEFAULT_IGNORE_FILENAME} at {discovered}",
                    file=sys.stderr,
                )
            allowlist_globs = _load_allowlist(str(discovered))

    found_any = False
    scanned = 0

    for file_str in args.files:
        if allowlist_globs and _is_allowlisted(file_str, allowlist_globs):
            continue

        scanned += 1
        matches = scan_file(Path(file_str))
        for match in matches:
            found_any = True
            print(
                f"{file_str}:{match.line}:{match.column} [{match.pattern_name}] {match.excerpt}",
                file=sys.stderr,
            )

    # --- Path-based checks (Story 6.8, FR39) ---

    violations: list[Violation] = []

    violations.extend(check_sensitive_paths(args.files))

    worktree_root = Path(args.worktree_root) if args.worktree_root else Path.cwd()
    violations.extend(check_worktree_boundary(args.files, worktree_root))

    for v in violations:
        print(v.message, file=sys.stderr)

    # --- License compatibility scan (Story 6.10, FR40) ---

    license_findings = scan_files_for_licenses(args.files, args.repo_license)
    for lf in license_findings:
        print(
            f"LICENSE {lf.reason_code}: {lf.file_path} ({lf.license_detected})",
            file=sys.stderr,
        )

    if found_any or violations or license_findings:
        return 1

    if args.verbose:
        print(f"OK: secret-hygiene ({scanned} files scanned, 0 matches)")

    return 0


def commit_msg_main(argv: list[str] | None = None) -> int:
    """Entrypoint for the ``commit-msg`` hook (Story 6.8).

    Checks the commit message file for injection patterns.
    Git passes the message file path as the first positional argument.
    """
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        return 0
    msg_path = Path(argv[0])
    violations = check_commit_message(msg_path)
    for v in violations:
        print(v.message, file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
