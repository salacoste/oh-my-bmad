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
import fnmatch
import sys
from pathlib import Path

from .scanner import scan_file

# ---------------------------------------------------------------------------
# Allowlist loading
# ---------------------------------------------------------------------------


def _load_allowlist(allowlist_path: str) -> list[str]:
    """Return glob patterns from *allowlist_path*, skipping comments + blanks."""
    globs: list[str] = []
    with open(allowlist_path, encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            globs.append(line)
    return globs


def _is_allowlisted(file_path: str, globs: list[str]) -> bool:
    """Return True if *file_path* matches any glob in *globs*."""
    return any(fnmatch.fnmatch(file_path, pattern) for pattern in globs)


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
        help="Path to a file with one glob pattern per line; matching files are skipped.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print a summary line when no secrets are found.",
    )

    args = parser.parse_args(argv)

    # Load allowlist globs (if provided).
    allowlist_globs: list[str] = []
    if args.allowlist_file is not None:
        allowlist_globs = _load_allowlist(args.allowlist_file)

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

    if found_any:
        return 1

    if args.verbose:
        print(f"✓ secret-hygiene OK ({scanned} files scanned, 0 matches)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
