"""crash-injection placeholder test — real tests land in Stories 2.11 / 2.12 (crash-injection harness).

Marker + skip-reason exist so CI passes on a bare tree and the
test-discovery surface is locked in from Story 1.5.
"""

from __future__ import annotations

import pytest


@pytest.mark.crash
@pytest.mark.skip(
    reason="placeholder — real tests land in Stories 2.11 / 2.12 (crash-injection harness)"
)
def test_placeholder() -> None:
    assert True
