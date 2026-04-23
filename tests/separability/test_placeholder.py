"""separability placeholder test — real tests land in S-1/S-2/S-3 land across Stories 2.15 / 5.16 / 5.17c.

Marker + skip-reason exist so CI passes on a bare tree and the
test-discovery surface is locked in from Story 1.5.
"""

from __future__ import annotations

import pytest


@pytest.mark.separability
@pytest.mark.skip(
    reason="placeholder — real tests land in S-1/S-2/S-3 land across Stories 2.15 / 5.16 / 5.17c"
)
def test_placeholder() -> None:
    assert True
