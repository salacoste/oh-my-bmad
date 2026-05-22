"""Fixture: unregister_all() with paired ensure_registered() — should pass RI001."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from events.schema_registry import register, unregister_all
from registry_state.domain.event_types import ensure_registered


@pytest.fixture(autouse=True)
def _clean_registry() -> Generator[None, None, None]:
    unregister_all()
    register("task.created", "1.0.0", object)
    yield
    unregister_all()
    ensure_registered()  # paired restore — gate accepts
