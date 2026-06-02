"""``replication.lagging`` event — Story 13.4 (NFR-R7 / FR69 / ADR-0007).

Emitted by ``scripts/check_replication_lag.py`` (run via ``just
litestream-lag-check``) when the litestream WAL-replication sidecar has stopped
making progress for longer than the sustained-lag window (5 min). The signal is
derived from the litestream ``/metrics`` Prometheus endpoint: ``litestream_sync_count``
STALLS (stops incrementing — normally ~1/s) while ``litestream_sync_error_count``
RISES. Recording the lag on the event spine closes the NFR-R7 observability gap
left open by Story 13.1's "sidecar without a healthcheck" decision: every
sustained replication stall becomes a permanent, replayable record on the same
JSONL log the rest of the platform consumes.

**Registration site**

Follows the ``packages/events/src/events/types/<domain>.py`` pattern pioneered
by ``events/types/deployment.py`` (Story 8.6). ``replication.lagging`` is an
operator-side event with no long-running Platform service emitter — the emitter
is a one-shot CLI helper — so the registration site stays inside
``packages/events`` (no circular-import cycle). See ``events/types/__init__.py``
for the rationale; the ``schema_registry.py`` docstring explicitly anticipates
this Epic 13 event type.

**Schema-version distinction**

Event-type ``schema_version`` registers at BOTH ``"1.0.0"`` and ``"1.1.0"``,
mirroring ``deployment.signature_rejected`` (Story 9.7 / AC10): both retained
for replay safety. The payload shape is identical across versions — this is a
NEW event type so there is no back-compat concern, but the same-model
registration idiom (additive-only, NFR-M3) is preserved. The envelope-level
``schema_version`` (set by ``EventEnvelope.create()``) is ``"1.1.0"`` since
Story 9.7 made ``trace_id`` required.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from events.schema_registry import register


class ReplicationLaggingPayload(BaseModel):
    """Payload for the ``replication.lagging`` event (Story 13.4 / NFR-R7).

    Emitted when ``just litestream-lag-check`` detects that the litestream
    sidecar has not advanced ``litestream_sync_count`` for the lagging database
    over a sustained window (> ``sustained_seconds``) while
    ``litestream_sync_error_count`` has been observed (the ``sync_stalled``
    signal). The payload captures enough detail for an operator to triage the
    stall and for downstream replay to reconstruct the replication-health
    timeline.

    Field rules:

    * ``db`` — the database PATH that is lagging (e.g.
      ``/var/lib/oh-my-bmad/registry/state.sqlite3``). ``min_length=1``;
      bounded to 4096 chars (filesystem path cap).
    * ``signal`` — the detection signal (bounded ``Literal`` so any new variant
      is a deliberate schema change, not a free-form typo):
      ``"sync_stalled"`` (sync_count stalled WHILE sync_error_count rose —
      S3/network failures) or ``"silent_stall"`` (sync_count stalled with errors
      FLAT — the sync loop itself is hung; Story 13.4a, additive on the same
      1.1.0 schema since it is a new enum *value*, not a field/shape change).
    * ``threshold_seconds`` — the lag threshold (the ~30s sampling/lag
      granularity). ``gt=0``.
    * ``sustained_seconds`` — how long the lag has been sustained when the
      event is emitted (>= the 300s sustained-lag window). ``ge=0``.
    * ``sync_error_count`` — the observed ``litestream_sync_error_count`` value
      at emit time. ``ge=0``.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    db: str = Field(min_length=1, max_length=4096)
    signal: Literal["sync_stalled", "silent_stall"]
    threshold_seconds: int = Field(gt=0)
    sustained_seconds: int = Field(ge=0)
    sync_error_count: int = Field(ge=0)


# Side-effect: register at module-load. Idempotent — re-import is a no-op per
# schema_registry.py:80. Test isolation strategies that call ``unregister_all()``
# must re-import this module (or call ``register(...)`` directly) to restore.
#
# Story 13.4: register both 1.0.0 and 1.1.0 (mirrors deployment.signature_rejected
# / Story 9.7 AC10) — both retained for replay safety. NEW event type, payload
# shape identical across versions (additive-only NFR-M3).
register(
    "replication.lagging",
    "1.0.0",
    ReplicationLaggingPayload,
)
register(
    "replication.lagging",
    "1.1.0",
    ReplicationLaggingPayload,
)


__all__ = ["ReplicationLaggingPayload"]
