"""migrator placeholder test — real tests land in Story 2.14 (migrator integration test).

Marker + skip-reason exist so CI passes on a bare tree and the
test-discovery surface is locked in from Story 1.5.
"""

from __future__ import annotations

import pytest


@pytest.mark.migrator
@pytest.mark.skip(reason="placeholder — real tests land in Story 2.14 (migrator integration test)")
def test_placeholder() -> None:
    assert True
