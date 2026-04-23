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

_NOQA_RE = re.compile(r"#\s*noqa:\s*([A-Z]+\d+)\b\s+(\S+.*)")


@dataclass
class Violation:
    file: Path
    lineno: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.lineno} [{self.rule}] {self.message}"


def has_noqa(source_line: str, tag: str) -> str | None:
    """Return the suppression reason if the line contains ``# noqa: TAG <reason>``.

    The reason must be non-empty (bare ``# noqa: TAG`` is not accepted).
    Returns the reason string on match, or None if not suppressed.
    """
    m = _NOQA_RE.search(source_line)
    if m and m.group(1) == tag:
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
            # Skip any path component that is in skip_dirs
            if any(part in skip_dirs for part in path.parts):
                continue
            yield path
