"""Tests for :class:`MetricsSubscriberSettings` (Story 10.2 AC6).

Coverage:

* defaults populate the documented production values
* ``OMB_METRICS_*`` env-var overrides applied
* validation: ``poll_interval_s`` rejects ≤ 0 and > 60
* validation: ``persist_every_n_events`` rejects < 1
* unrelated env vars do not bleed in (``extra="ignore"``)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from metrics_subscriber.app.config import MetricsSubscriberSettings


def test_defaults_match_acceptance_criteria() -> None:
    settings = MetricsSubscriberSettings()
    assert settings.event_log_dir == Path("/var/lib/oh-my-bmad/registry/events")
    assert settings.cursor_path == Path("/var/lib/oh-my-bmad/metrics-subscriber/cursor.json")
    assert settings.poll_interval_s == 0.5
    assert settings.persist_every_n_events == 1000


def test_env_overrides_take_effect() -> None:
    os.environ["OMB_METRICS_EVENT_LOG_DIR"] = "/tmp/test-events"
    os.environ["OMB_METRICS_CURSOR_PATH"] = "/tmp/cursor.json"
    os.environ["OMB_METRICS_POLL_INTERVAL_S"] = "2.5"
    os.environ["OMB_METRICS_PERSIST_EVERY_N_EVENTS"] = "42"
    settings = MetricsSubscriberSettings()
    assert settings.event_log_dir == Path("/tmp/test-events")
    assert settings.cursor_path == Path("/tmp/cursor.json")
    assert settings.poll_interval_s == 2.5
    assert settings.persist_every_n_events == 42


def test_poll_interval_must_be_positive() -> None:
    os.environ["OMB_METRICS_POLL_INTERVAL_S"] = "0"
    with pytest.raises(ValidationError):
        MetricsSubscriberSettings()


def test_poll_interval_must_be_le_60() -> None:
    os.environ["OMB_METRICS_POLL_INTERVAL_S"] = "60.1"
    with pytest.raises(ValidationError):
        MetricsSubscriberSettings()


def test_persist_every_must_be_at_least_one() -> None:
    os.environ["OMB_METRICS_PERSIST_EVERY_N_EVENTS"] = "0"
    with pytest.raises(ValidationError):
        MetricsSubscriberSettings()


def test_unrelated_env_vars_ignored() -> None:
    os.environ["OMB_METRICS_UNKNOWN_FUTURE_FIELD"] = "x"
    settings = MetricsSubscriberSettings()
    # Should not raise; the unknown field should be silently dropped.
    assert settings.poll_interval_s == 0.5


def test_poll_interval_rejects_nan() -> None:
    """VM-4 — ``nan`` must not be accepted (would crash asyncio.sleep)."""
    os.environ["OMB_METRICS_POLL_INTERVAL_S"] = "nan"
    with pytest.raises(ValidationError):
        MetricsSubscriberSettings()


def test_poll_interval_rejects_inf() -> None:
    """VM-4 — ``inf`` must not be accepted (would hang asyncio.sleep)."""
    os.environ["OMB_METRICS_POLL_INTERVAL_S"] = "inf"
    with pytest.raises(ValidationError):
        MetricsSubscriberSettings()
