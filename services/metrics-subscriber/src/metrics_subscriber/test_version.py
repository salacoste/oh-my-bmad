"""Smoke test: package imports + version string is non-empty and well-formed.

Strengthened per Story 10.1 pass-1 review (B1, B4):
- B1: `isdigit()` accepts leading zeros (invalid semver); replaced with regex
- B4: `__version__` drift undetectable; cross-check against installed metadata
"""

from __future__ import annotations

import re
from importlib.metadata import version

from metrics_subscriber import __version__

# Strict semver per https://semver.org §2 — no leading zeros except bare "0".
_SEMVER_PART_RE = re.compile(r"\A(0|[1-9]\d*)\Z")


def test_version_is_non_empty_string() -> None:
    assert isinstance(__version__, str)
    assert __version__  # not empty


def test_version_matches_semver_shape() -> None:
    """Strict semver MAJOR.MINOR.PATCH; rejects leading zeros (B1 fix)."""
    parts = __version__.split(".")
    assert len(parts) == 3, f"expected 3 semver parts, got {parts!r}"
    for part in parts:
        assert _SEMVER_PART_RE.match(part), (
            f"semver part {part!r} invalid (leading zeros disallowed except for bare '0')"
        )


def test_version_matches_pyproject() -> None:
    """Cross-check `__version__` against installed package metadata (B4 fix).

    Catches drift where pyproject.toml `version` is bumped without updating
    `__init__.py`'s `__version__` constant (or vice versa).
    """
    installed = version("metrics-subscriber")
    assert __version__ == installed, (
        f"version drift: __init__.py says {__version__!r}, "
        f"installed package metadata says {installed!r}"
    )
