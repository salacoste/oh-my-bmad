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

import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Pattern table — single source of truth shared with sanitizer.py
# ---------------------------------------------------------------------------

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "ANTHROPIC_API_KEY": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "TELEGRAM_BOT_TOKEN": re.compile(r"\b\d{6,12}:AA[A-Za-z0-9_\-]{30,}\b"),
    "GITHUB_TOKEN_CLASSIC": re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    "GITHUB_TOKEN_FINE": re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),
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
# Core scanning functions
# ---------------------------------------------------------------------------


def scan_text(text: str) -> list[SecretMatch]:
    """Scan *text* for all registered secret patterns.

    Returns one :class:`SecretMatch` per hit (across all patterns).
    Results are ordered by match start position within the text.
    """
    matches: list[SecretMatch] = []

    for pattern_name, pattern in SECRET_PATTERNS.items():
        for m in pattern.finditer(text):
            # Compute 1-based line + column from the flat character offset.
            prefix = text[: m.start()]
            line = prefix.count("\n") + 1
            last_newline = prefix.rfind("\n")
            column = m.start() - last_newline  # rfind returns -1 if no \n → col = start+1

            matches.append(
                SecretMatch(
                    pattern_name=pattern_name,
                    start=m.start(),
                    end=m.end(),
                    line=line,
                    column=column,
                    excerpt=f"<{pattern_name}>",
                )
            )

    matches.sort(key=lambda sm: sm.start)
    return matches


def scan_file(path: Path) -> list[SecretMatch]:
    """Read *path* and return all secret matches, or ``[]`` on read failure.

    Silently returns an empty list for binary files (``UnicodeDecodeError``) and
    files that cannot be read (``OSError`` / ``PermissionError`` / etc.).  This
    keeps the pre-commit hook non-fatal on binary blobs that slip past
    pre-commit's ``types: [text]`` filter.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    return scan_text(text)
