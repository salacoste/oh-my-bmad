"""Path-based pre-commit validation checks (Story 6.8, FR39).

Three orthogonal checks that run alongside the existing content-based
secret scanner:

1. **Sensitive path** — blocks commits touching ``.env*``, ``secrets/``,
   ``*.pem``, ``*.key``, ``*.credentials*``.
2. **Worktree boundary** — blocks commits with files outside the assigned
   worktree (catches symlink escapes).
3. **Commit-message injection** — blocks null bytes and command substitution
   patterns in commit messages.

All checks return ``list[Violation]``; the caller prints and exits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Violation — result type shared by all checkers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    """A single check failure."""

    file_path: str
    rule: str
    message: str


# ---------------------------------------------------------------------------
# Sensitive path patterns
# ---------------------------------------------------------------------------

_SENSITIVE_PATH_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(^|/)\.env($|[/.])"), "sensitive-path"),
    (re.compile(r"(^|/)secrets/"), "sensitive-path"),
    (re.compile(r"\.pem$"), "sensitive-path"),
    (re.compile(r"\.key$"), "sensitive-path"),
    (re.compile(r"\.credentials"), "sensitive-path"),
]


def check_sensitive_paths(staged_files: list[str]) -> list[Violation]:
    """Return violations for staged files matching sensitive path patterns."""
    violations: list[Violation] = []
    for fp in staged_files:
        for pattern, rule in _SENSITIVE_PATH_RULES:
            if pattern.search(fp):
                violations.append(
                    Violation(
                        file_path=fp,
                        rule=rule,
                        message=f"Refusing to commit sensitive path: {fp}",
                    )
                )
                break
    return violations


# ---------------------------------------------------------------------------
# Worktree boundary check
# ---------------------------------------------------------------------------


def check_worktree_boundary(
    staged_files: list[str],
    worktree_root: Path,
) -> list[Violation]:
    """Return violations for files outside the assigned worktree.

    Resolves symlinks — if a symlink inside the worktree points outside,
    it is flagged as a boundary violation.
    """
    resolved_root = worktree_root.resolve()
    violations: list[Violation] = []
    for fp in staged_files:
        p = Path(fp)
        if not p.exists():
            continue
        resolved = p.resolve()
        if not p.exists():
            # Broken symlink — resolve() returns a non-existent path.
            continue
        if not resolved.is_relative_to(resolved_root):
            violations.append(
                Violation(
                    file_path=fp,
                    rule="worktree-boundary",
                    message=(
                        f"Refusing to commit file outside assigned worktree: "
                        f"{fp} (resolves to {resolved})"
                    ),
                )
            )
    return violations


# ---------------------------------------------------------------------------
# Commit-message injection check
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\x00"), "null-byte"),
    # $() is the actual shell command-substitution syntax. Backticks in
    # commit messages are almost always Markdown inline code, not injection.
    (re.compile(r"\$\([^)]+\)"), "command-substitution"),
]


def check_commit_message(msg_path: Path) -> list[Violation]:
    """Return violations for injection patterns in a commit message file.

    Returns an empty list if the file does not exist (non-fatal).
    """
    try:
        text = msg_path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError):
        return []

    violations: list[Violation] = []
    seen: set[str] = set()
    for pattern, rule in _INJECTION_PATTERNS:
        for m in pattern.finditer(text):
            key = f"{rule}:{m.start()}"
            if key not in seen:
                seen.add(key)
                label = "null byte" if rule == "null-byte" else "command substitution"
                violations.append(
                    Violation(
                        file_path=str(msg_path),
                        rule=rule,
                        message=f"Commit message contains {label} at position {m.start()}",
                    )
                )
    return violations
