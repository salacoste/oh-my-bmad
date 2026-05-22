"""Violation: unregister_all() in teardown with no restore — should fail RI001."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from events.schema_registry import unregister_all


@pytest.fixture(autouse=True)
def _bad_teardown() -> Generator[None, None, None]:
    yield
    unregister_all()  # no ensure_registered() / register() / snapshot restore
