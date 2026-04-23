"""integration placeholder test — real tests land in Stories 5.18 / 7.9 / 7.10 (journey tests).

Marker + skip-reason exist so CI passes on a bare tree and the
test-discovery surface is locked in from Story 1.5.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
@pytest.mark.skip(
    reason="placeholder — real tests land in Stories 5.18 / 7.9 / 7.10 (journey tests)"
)
def test_placeholder() -> None:
    assert True
