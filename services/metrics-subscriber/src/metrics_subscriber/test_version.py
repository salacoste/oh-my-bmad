"""Smoke test: package imports + version string is non-empty."""

from __future__ import annotations

from metrics_subscriber import __version__


def test_version_is_non_empty_string() -> None:
    assert isinstance(__version__, str)
    assert __version__  # not empty


def test_version_matches_semver_shape() -> None:
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
