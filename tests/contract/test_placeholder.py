"""contract placeholder test — real tests land in Stories 2.8 / 5.10 (upstream-fork contract tests).

Marker + skip-reason exist so CI passes on a bare tree and the
test-discovery surface is locked in from Story 1.5.
"""

from __future__ import annotations

import pytest


@pytest.mark.contract
@pytest.mark.skip(
    reason="placeholder — real tests land in Stories 2.8 / 5.10 (upstream-fork contract tests)"
)
def test_placeholder() -> None:
    assert True
