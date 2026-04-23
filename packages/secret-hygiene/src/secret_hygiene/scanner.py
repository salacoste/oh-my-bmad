"""Secret-pattern scanner for pre-commit enforcement and runtime hygiene.

MVP pattern set (five entries). This module is the SINGLE SOURCE OF TRUTH for
pattern definitions — ``sanitizer.py`` imports ``SECRET_PATTERNS`` directly so
both the pre-commit hook (source-control arm) and the structlog processor
(runtime/observability arm) enforce the same rules.

Pattern rationale
-----------------
* ``ANTHROPIC_API_KEY`` (``sk-ant-...``) — Claude API; the operator's primary
  critical secret.  Rotating it costs downtime.
* ``TELEGRAM_BOT_TOKEN`` (``<numeric-id>:AA<blob>``) — Telegram bot credential.
  A leaked token lets an attacker impersonate the bot and DM every allowlisted
  user.
* ``GITHUB_TOKEN_CLASSIC`` (``ghp_...``) — classic GitHub PAT; controls the
  operator's repo for PR-draft creation (FR5.14).
* ``GITHUB_TOKEN_FINE`` (``github_pat_...``) — fine-grained GitHub PAT; same
  risk surface as the classic token.
* ``GENERIC_AWS_ACCESS_KEY`` (``AKIA...``) — defensive catch-all; Phase 1
  doesn't wire AWS directly, but accidentally committed third-party credentials
  are realistic.

Intentionally NOT in the MVP set (see spec §Dev Notes):
- Generic ``password=...`` — too many false positives in YAML/docstrings.
- SSH private keys — covered better by dedicated tools such as ``gitleaks``.
- Slack tokens — Phase 7 adds Slack; deferred.
"""

from __future__ import annotations

import bisect
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Pattern table — single source of truth shared with sanitizer.py
#
# Regex notes (review fixes applied):
#   ANTHROPIC_API_KEY    — left-boundary (?<![A-Za-z0-9]) prevents prefix
#                          matches; upper-bounded at 200 chars to avoid greedy
#                          absorption of adjacent tokens.
#   TELEGRAM_BOT_TOKEN   — replaced trailing \b with (?=[^A-Za-z0-9_\-]|$)
#                          lookahead; the token's allowed charset includes `-`
#                          (non-word), so \b could miss tokens ending in `-`.
#   GITHUB_TOKEN_CLASSIC — left-boundary added; upper-bounded at 100 chars.
#   GITHUB_TOKEN_FINE    — left-boundary added; upper-bounded at 200 chars.
#   GENERIC_AWS_ACCESS_KEY — unchanged (AKIA prefix + exact 16 uppercase chars
#                            already well-bounded by \b).
# ---------------------------------------------------------------------------

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "ANTHROPIC_API_KEY": re.compile(r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_\-]{20,200}"),
    "TELEGRAM_BOT_TOKEN": re.compile(
        r"(?<![A-Za-z0-9])\d{6,12}:AA[A-Za-z0-9_\-]{30,}(?=[^A-Za-z0-9_\-]|$)"
    ),
    "GITHUB_TOKEN_CLASSIC": re.compile(r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{30,100}"),
    "GITHUB_TOKEN_FINE": re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{30,200}"),
    "GENERIC_AWS_ACCESS_KEY": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}


# ---------------------------------------------------------------------------
# SecretMatch — result type (immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecretMatch:
    """Describes a single secret detected in text.

    Attributes
    ----------
    pattern_name:
        Key in ``SECRET_PATTERNS`` that fired (e.g. ``"ANTHROPIC_API_KEY"``).
    start:
        Character offset of the match start within the scanned text.
    end:
        Character offset of the match end (exclusive) within the scanned text.
    line:
        1-based line number where the match starts.
    column:
        1-based column number where the match starts on that line.
    excerpt:
        Safe representation of the match — never contains the raw secret value.
        Format: ``<PATTERN_NAME>`` so callers can identify the pattern without
        any risk of leaking the secret through error messages or logs.
    """

    pattern_name: str
    start: int
    end: int
    line: int
    column: int
    excerpt: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_line_starts(text: str) -> list[int]:
    """Return a list of character offsets where each line starts (O(n)).

    The list is 0-indexed: ``line_starts[0]`` is always ``0`` (start of line 1),
    ``line_starts[1]`` is the character offset of line 2, etc.
    """
    starts: list[int] = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _line_col(offset: int, line_starts: list[int]) -> tuple[int, int]:
    """Convert a character *offset* to a 1-based ``(line, column)`` pair.

    Uses a pre-computed *line_starts* list produced by
    :func:`_build_line_starts` for O(log n) per call.
    """
    line_idx = bisect.bisect_right(line_starts, offset) - 1
    line = line_idx + 1
    col = offset - line_starts[line_idx] + 1
    return line, col


# ---------------------------------------------------------------------------
# Core scanning functions
# ---------------------------------------------------------------------------


def scan_text(text: str) -> list[SecretMatch]:
    """Scan *text* for all registered secret patterns.

    Returns one :class:`SecretMatch` per hit (across all patterns), deduplicated
    by ``(start, end)`` span so overlapping patterns don't double-report the same
    character range.  Results are ordered by match start position.
    """
    # Build the line-starts index once; O(n) in text length.
    line_starts = _build_line_starts(text)

    raw_matches: list[SecretMatch] = []

    for pattern_name, pattern in SECRET_PATTERNS.items():
        for m in pattern.finditer(text):
            line, col = _line_col(m.start(), line_starts)
            raw_matches.append(
                SecretMatch(
                    pattern_name=pattern_name,
                    start=m.start(),
                    end=m.end(),
                    line=line,
                    column=col,
                    excerpt=f"<{pattern_name}>",
                )
            )

    # Deduplicate by (start, end); keep first-encountered pattern for each span.
    raw_matches.sort(key=lambda sm: (sm.start, sm.end))
    seen: set[tuple[int, int]] = set()
    unique: list[SecretMatch] = []
    for sm in raw_matches:
        key = (sm.start, sm.end)
        if key in seen:
            continue
        seen.add(key)
        unique.append(sm)

    return unique


def scan_file(path: Path) -> list[SecretMatch]:
    """Read *path* and return all secret matches.

    * Binary files (``UnicodeDecodeError``) → return ``[]`` silently.
    * Missing file (``FileNotFoundError``) → print warning to stderr, return ``[]``.
    * Other I/O errors (``OSError``) → print warning to stderr, return ``[]``.

    Printing to stderr keeps the pre-commit hook non-fatal on transient issues
    while ensuring operators see the problem.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    except FileNotFoundError:
        print(f"warning: secret-hygiene: file not found: {path}", file=sys.stderr)
        return []
    except OSError as exc:
        print(f"warning: secret-hygiene: cannot read {path}: {exc}", file=sys.stderr)
        return []
    return scan_text(text)
