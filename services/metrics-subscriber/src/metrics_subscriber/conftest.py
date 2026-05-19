"""Test isolation fixtures for metrics-subscriber (Story 10.2 AC10).

Mirrors the PH-H6 pattern established in Story 9.7: clear all
``OMB_METRICS_*`` environment variables between tests so an ambient
override in the developer shell (e.g. ``OMB_METRICS_POLL_INTERVAL_S=10``)
cannot leak into tests and silently change observed behaviour.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def _clear_omb_metrics_env() -> Generator[None, None, None]:
    """Autouse fixture: strip every ``OMB_METRICS_*`` env var around each test.

    Saves + restores so other tests in the session still see whatever
    the developer had configured before pytest started.
    """
    keys = [k for k in os.environ if k.startswith("OMB_METRICS_")]
    saved = {k: os.environ.pop(k) for k in keys}
    try:
        yield
    finally:
        # Remove anything the test set + restore the original mapping.
        for k in [k for k in os.environ if k.startswith("OMB_METRICS_")]:
            os.environ.pop(k, None)
        os.environ.update(saved)
