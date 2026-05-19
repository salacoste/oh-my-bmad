"""Pydantic settings for the β metrics-subscriber service (Story 10.2 AC6).

Settings are populated from environment variables under the ``OMB_METRICS_``
prefix:

  OMB_METRICS_EVENT_LOG_DIR        — JSONL event-log root (default
                                     ``/var/lib/oh-my-bmad/registry/events``).
  OMB_METRICS_CURSOR_PATH          — Persisted cursor file
                                     (default ``/var/lib/oh-my-bmad/
                                     metrics-subscriber/cursor.json``).
  OMB_METRICS_POLL_INTERVAL_S      — Tail-loop poll cadence in seconds
                                     (default ``0.5``; validated bounds
                                     ``0 < x ≤ 60``).
  OMB_METRICS_PERSIST_EVERY_N_EVENTS
                                   — Atomic cursor-persist cadence
                                     (default ``1000``; validated ``≥ 1``).

Validation is intentional: ``poll_interval_s`` outside ``(0, 60]`` or
``persist_every_n_events < 1`` would either thrash the file system or
silently drop the resumability guarantee (AC4).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MetricsSubscriberSettings(BaseSettings):
    """Settings for the metrics-subscriber lifespan task (AC6)."""

    # VM-4: ``allow_inf_nan=False`` rejects ``OMB_METRICS_POLL_INTERVAL_S=nan``
    # / ``inf`` at validation time; otherwise ``asyncio.sleep(nan)`` would
    # crash the tail loop at runtime.
    model_config = SettingsConfigDict(
        env_prefix="OMB_METRICS_",
        extra="ignore",
        allow_inf_nan=False,
    )

    event_log_dir: Path = Path("/var/lib/oh-my-bmad/registry/events")
    cursor_path: Path = Path("/var/lib/oh-my-bmad/metrics-subscriber/cursor.json")
    poll_interval_s: float = Field(default=0.5, gt=0, le=60)
    persist_every_n_events: int = Field(default=1000, ge=1)


__all__ = ["MetricsSubscriberSettings"]
