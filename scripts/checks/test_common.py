"""Unit tests for has_noqa() in scripts/checks/_common.py."""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts/ to sys.path so checks._common is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from checks._common import has_noqa  # noqa: E402


def test_single_tag_match() -> None:
    line = "from os import path  # noqa: IMP001 — lazy import"
    assert has_noqa(line, "IMP001") == "— lazy import"


def test_single_tag_no_match() -> None:
    line = "from os import path  # noqa: IMP001 — lazy import"
    assert has_noqa(line, "SHELL001") is None


def test_multi_tag_match_first() -> None:
    line = "from registry_state import __version__  # noqa: PLC0415, IMP001 — multi-tag"
    assert has_noqa(line, "PLC0415") == "— multi-tag"


def test_multi_tag_match_second() -> None:
    line = "from registry_state import __version__  # noqa: PLC0415, IMP001 — multi-tag"
    assert has_noqa(line, "IMP001") == "— multi-tag"


def test_multi_tag_no_match() -> None:
    line = "from registry_state import __version__  # noqa: PLC0415, IMP001 — multi-tag"
    assert has_noqa(line, "SHELL001") is None


def test_bare_noqa_without_reason_returns_none() -> None:
    line = "from os import path  # noqa: IMP001"
    assert has_noqa(line, "IMP001") is None


def test_bare_noqa_no_tags_returns_none() -> None:
    line = "from os import path  # noqa:"
    assert has_noqa(line, "IMP001") is None


def test_no_noqa_at_all_returns_none() -> None:
    line = "from os import path  # just a regular comment"
    assert has_noqa(line, "IMP001") is None


def test_case_insensitive_noqa_keyword() -> None:
    line = "from os import path  # NOQA: IMP001 — uppercase keyword"
    assert has_noqa(line, "IMP001") == "— uppercase keyword"


def test_case_insensitive_noqa_mixed() -> None:
    line = "from os import path  # NoQa: IMP001 — mixed case"
    assert has_noqa(line, "IMP001") == "— mixed case"


def test_tag_identifiers_case_sensitive() -> None:
    line = "from os import path  # noqa: IMP001 — reason"
    assert has_noqa(line, "imp001") is None
