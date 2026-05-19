"""Test isolation fixtures for metrics-subscriber (Story 10.2 AC10).

Mirrors the PH-H6 pattern established in Story 9.7: clear all
``OMB_METRICS_*`` environment variables between tests so an ambient
override in the developer shell (e.g. ``OMB_METRICS_POLL_INTERVAL_S=10``)
cannot leak into tests and silently change observed behaviour.

VM-5: ``structlog`` is configured to route through stdlib ``logging``
so ``pytest`` ``caplog`` captures structured events emitted by the
subscriber code paths.  Production main configures its own structlog
pipeline; in tests we want a minimal config that bridges to stdlib.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator

import pytest
import structlog


def _configure_structlog_for_tests() -> None:
    """Wire structlog through stdlib so ``caplog`` captures events."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.KeyValueRenderer(key_order=["event", "level"], sort_keys=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


_configure_structlog_for_tests()


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
