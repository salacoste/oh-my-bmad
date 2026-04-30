"""Shared helpers for the three CI-gate check scripts.

Provides:
  - Violation dataclass
  - has_noqa() for per-line suppression parsing
  - walk_python_files() for skipping vendor/cache dirs
  - DEFAULT_SKIP_DIRS constant
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "upstream",
        ".venv",
        ".uv",
        "_bmad",
        "_bmad-output",
        "node_modules",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tmp",
        ".agent",
        ".agents",
        ".claude",
        ".cursor",
        ".gemini",
        ".opencode",
        ".pi",
        ".omc",
        "__pycache__",
    }
)

# Story 3.8 review H9: multi-tag noqa support.
#
# The previous regex captured only the FIRST tag, so a comment like
# ``# noqa: PLC0415, SHELL001 — reason`` silently failed to suppress
# ``SHELL001``. The Story 5.4 worker-wrapper subprocess call site needs
# both ``IMP001`` (cross-service import) and ``SHELL001`` (legitimate
# shell escape) on the same line; without the multi-tag fix that combined
# suppression would not parse.
#
# New shape:
#   * Capture group 1 = comma-separated tag list (one or more ``[A-Z]+\d+``)
#   * Capture group 2 = the reason text (must be non-empty)
#   * ``noqa:`` is matched case-insensitively (Story 3.8 review M10) — ruff
#     itself treats the noqa keyword case-insensitively.
#   * Tag identifiers remain case-sensitive so ``shell001`` would not
#     accidentally suppress ``SHELL001``.
_NOQA_RE = re.compile(
    r"#\s*noqa:\s*([A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*)\b\s+(\S+.*)",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"[A-Z]+\d+")


@dataclass
class Violation:
    file: Path
    lineno: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.lineno} [{self.rule}] {self.message}"


def has_noqa(source_line: str, tag: str) -> str | None:
    """Return the suppression reason if the line contains ``# noqa: TAG[,…] <reason>``.

    The reason must be non-empty (bare ``# noqa: TAG`` is not accepted).
    Returns the reason string on match, or None if not suppressed.

    Story 3.8 review H9: supports multi-tag suppressions of the form
    ``# noqa: PLC0415, SHELL001 — reason``. The reason is the same for all
    tags on the line (per ruff convention).

    Story 3.8 review L3: when a single line contains multiple violations
    (cross-service import AND subprocess call), the caller is responsible
    for invoking ``has_noqa`` with each tag — this helper checks one tag at
    a time.
    """
    m = _NOQA_RE.search(source_line)
    if not m:
        return None
    tags = {t.strip() for t in _TAG_RE.findall(m.group(1))}
    if tag in tags:
        return m.group(2).strip()
    return None


def walk_python_files(
    roots: Iterable[Path],
    *,
    skip_dirs: set[str] | frozenset[str] = DEFAULT_SKIP_DIRS,
) -> Iterator[Path]:
    """Yield every ``.py`` file under *roots*, skipping named directories.

    If a root is itself a ``.py`` file it is yielded directly (used by
    self-test harnesses that pass individual fixture file paths).
    """
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix == ".py":
                yield root
            continue
        for path in root.rglob("*.py"):
            # Check ONLY components inside the root, not absolute path components
            # above it. Prevents catastrophic false-clean when the repo is checked
            # out under a dotted-ancestor path like ~/.claude/workspaces/…
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if any(part in skip_dirs for part in rel.parts):
                continue
            yield path
