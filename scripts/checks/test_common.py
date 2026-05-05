# Run: uv run pytest scripts/checks/test_common.py
"""Unit tests for has_noqa() in scripts/checks/_common.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add scripts/ to sys.path so checks._common is importable.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _SCRIPTS_DIR)

from checks._common import has_noqa  # noqa: E402

# Remove the path entry after import to avoid polluting global state.
sys.path.remove(_SCRIPTS_DIR)


@pytest.fixture()
def _on_path() -> None:
    """Make checks._common importable for the duration of each test."""
    sys.path.insert(0, _SCRIPTS_DIR)
    yield
    sys.path.remove(_SCRIPTS_DIR)


@pytest.mark.usefixtures("_on_path")
class TestSingleTag:
    def test_match(self) -> None:
        line = "from os import path  # noqa: IMP001 — lazy import"
        assert has_noqa(line, "IMP001") == "— lazy import"

    def test_no_match(self) -> None:
        line = "from os import path  # noqa: IMP001 — lazy import"
        assert has_noqa(line, "SHELL001") is None


@pytest.mark.usefixtures("_on_path")
class TestMultiTag:
    def test_match_first(self) -> None:
        line = "from registry_state import __version__  # noqa: PLC0415, IMP001 — multi-tag"
        assert has_noqa(line, "PLC0415") == "— multi-tag"

    def test_match_second(self) -> None:
        line = "from registry_state import __version__  # noqa: PLC0415, IMP001 — multi-tag"
        assert has_noqa(line, "IMP001") == "— multi-tag"

    def test_no_match(self) -> None:
        line = "from registry_state import __version__  # noqa: PLC0415, IMP001 — multi-tag"
        assert has_noqa(line, "SHELL001") is None

    def test_three_tags(self) -> None:
        line = "x  # noqa: PLC0415, IMP001, SHELL001 — triple"
        assert has_noqa(line, "SHELL001") == "— triple"
        assert has_noqa(line, "PLC0415") == "— triple"


@pytest.mark.usefixtures("_on_path")
class TestBareNoqa:
    def test_without_reason_returns_none(self) -> None:
        line = "from os import path  # noqa: IMP001"
        assert has_noqa(line, "IMP001") is None

    def test_no_tags_returns_none(self) -> None:
        line = "from os import path  # noqa:"
        assert has_noqa(line, "IMP001") is None

    def test_no_noqa_at_all_returns_none(self) -> None:
        line = "from os import path  # just a regular comment"
        assert has_noqa(line, "IMP001") is None


@pytest.mark.usefixtures("_on_path")
class TestCaseSensitivity:
    def test_uppercase_keyword(self) -> None:
        line = "from os import path  # NOQA: IMP001 — uppercase keyword"
        assert has_noqa(line, "IMP001") == "— uppercase keyword"

    def test_mixed_case_keyword(self) -> None:
        line = "from os import path  # NoQa: IMP001 — mixed case"
        assert has_noqa(line, "IMP001") == "— mixed case"

    def test_tag_identifiers_case_sensitive(self) -> None:
        line = "from os import path  # noqa: IMP001 — reason"
        assert has_noqa(line, "imp001") is None


@pytest.mark.usefixtures("_on_path")
class TestReasonShapes:
    def test_reason_without_em_dash(self) -> None:
        line = "from os import path  # noqa: IMP001 plain reason text"
        assert has_noqa(line, "IMP001") == "plain reason text"

    def test_trailing_whitespace_stripped(self) -> None:
        line = "from os import path  # noqa: IMP001 — reason   "
        assert has_noqa(line, "IMP001") == "— reason"


@pytest.mark.usefixtures("_on_path")
class TestBoundaryInputs:
    def test_empty_string_returns_none(self) -> None:
        assert has_noqa("", "IMP001") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert has_noqa("    ", "IMP001") is None


@pytest.mark.usefixtures("_on_path")
class TestDoubleNoqa:
    """Two noqa comments on one line: regex.search() matches the first greedily.

    This is a known limitation — the second noqa is swallowed into group 2 of
    the first match. Production code never emits double-noqa lines, but this
    test documents the behaviour for future maintainers.
    """

    def test_first_noqa_captures_greedily(self) -> None:
        line = "x  # noqa: IMP001  # noqa: SHELL001 — reason"
        assert has_noqa(line, "IMP001") == "# noqa: SHELL001 — reason"

    def test_second_noqa_not_matched(self) -> None:
        line = "x  # noqa: IMP001  # noqa: SHELL001 — reason"
        assert has_noqa(line, "SHELL001") is None
