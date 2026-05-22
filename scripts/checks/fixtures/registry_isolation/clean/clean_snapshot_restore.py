"""Fixture: snapshot/restore pattern — should pass RI001."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from events.schema_registry import REGISTRY, _rebuild_types_cache, unregister_all


@pytest.fixture
def _isolated_registry() -> Generator[None, None, None]:
    snapshot = dict(REGISTRY)
    unregister_all()
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)
    _rebuild_types_cache()
