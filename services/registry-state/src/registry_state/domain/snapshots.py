"""Snapshot capture — periodic checkpoints of materialized state (Story 2.6).

The ``SnapshotPolicy`` class accumulates an ``_events_since_snapshot`` tally
(driven by ``apply_many`` callers passing the count of newly-applied events).
Once the tally meets or exceeds ``interval`` the next ``maybe_capture`` call
takes a snapshot of all ``tasks`` + ``sessions`` rows plus the ``events`` row
count, serializes them to a versioned canonical-JSON payload, and inserts a
single row into the ``snapshots`` table.

Capture is the WRITE path (during normal subscriber operation). Restore is
the READ path (at startup) and lives in :mod:`registry_state.domain.recovery`.
The two modules communicate solely via the ``snapshots`` table — no direct
calls between them.

Atomicity contract
------------------
A single ``async with session_maker() as session, session.begin():`` block
covers the read-tasks / read-sessions / count-events / INSERT sequence. The
transaction is the snapshot's consistency boundary: if any step raises, the
whole snapshot is rolled back and no partial row reaches the database.

Idempotency
-----------
Snapshots are NOT keyed on ``cursor_event_id``; every capture inserts a new
row with a fresh UUIDv7 id. Replaying the subscriber loop produces fresh
snapshots — that's fine, snapshots are checkpoints, not part of the event
log's source-of-truth contract.

Retention (AC-10) — Phase 1: keep-all-forever
---------------------------------------------
The ``snapshots`` table accumulates indefinitely. At a default 1000-event
interval and a 10-events/s steady state that's roughly one snapshot per 100
seconds × 24 h × 365 d ≈ 315 000 snapshots/year × ≈5 KB/snapshot ≈ 1.5 GB/year.
Acceptable for Phase 1; pruning is deferred to a future story (operator-driven
``DELETE`` is fine in the meantime). Diff-based / incremental snapshots are a
Phase-4 optimization.

Payload schema v1
-----------------
Top-level keys (alphabetical via ``sort_keys=True``):

  * ``cursor_emitted_at_monotonic_ns`` — ``int``; the cursor anchor for replay.
  * ``sessions`` — list of session-row dicts.
  * ``tasks`` — list of task-row dicts.
  * ``version`` — ``int`` (``1``).

Datetimes serialize via ``events.canonical._default_encoder`` (UTC ISO 8601
with ``Z`` suffix and millisecond precision) — the same canonical encoder the
event log uses, so round-trip reads are byte-stable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from events.canonical import _default_encoder  # reuse canonical encoder
from events.clock import Clock
from events.envelope import EventEnvelope
from events.ids import new_uuid7
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from registry_state.schema import Event, Snapshot, Task
from registry_state.schema import Session as SessionRow

log = logging.getLogger(__name__)


def _datetime_to_iso(dt: datetime | None) -> str | None:
    """Serialize a UTC-aware datetime to canonical ISO 8601 ``Z`` form, or pass ``None`` through.

    Reuses the canonical encoder so the textual form matches the event log
    byte-for-byte (millisecond precision, ``Z`` suffix, no offset).
    """
    if dt is None:
        return None
    iso = _default_encoder(dt)
    assert isinstance(iso, str)
    return iso


def _datetime_from_iso(value: str | None) -> datetime | None:
    """Parse a canonical ISO 8601 ``Z`` string back into a UTC-aware datetime.

    The inverse of :func:`_datetime_to_iso`. Replaces the trailing ``Z`` with
    ``+00:00`` so :meth:`datetime.fromisoformat` handles it; the result is
    UTC-aware. ``None`` passes through unchanged so nullable columns survive
    the round-trip.
    """
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _str_field(d: dict[str, object], key: str) -> str:
    """Extract a required str-typed field from an untrusted dict.

    Raises ``ValueError`` if the field is missing or not a string. Used by the
    snapshot-payload deserializers so a malformed payload fails loudly rather
    than corrupting the materialized state via a wrong-type column write.
    """
    if key not in d:
        raise ValueError(f"required field {key!r} is missing")
    v = d[key]
    if not isinstance(v, str):
        raise ValueError(f"field {key!r} must be str; got {type(v).__name__}")
    return v


def _opt_str_field(d: dict[str, object], key: str) -> str | None:
    """Extract an optional str-typed field from an untrusted dict.

    Returns ``None`` if the field is absent or explicitly ``None``; raises
    ``ValueError`` if present-but-wrong-type so a malformed payload fails
    loudly.
    """
    v = d.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError(f"field {key!r} must be str or None; got {type(v).__name__}")
    return v


def _task_to_dict(t: Task) -> dict[str, object]:
    """Serialize a Task ORM row to a JSON-safe dict.

    Datetime columns are normalized to canonical ISO ``Z`` text via
    :func:`_datetime_to_iso`; everything else round-trips through
    ``getattr``.
    """
    return {
        "id": t.id,
        "status": t.status,
        "created_at": _datetime_to_iso(t.created_at),
        "updated_at": _datetime_to_iso(t.updated_at),
        "actor_kind": t.actor_kind,
        "actor_id": t.actor_id,
        "title": t.title,
        "last_event_id": t.last_event_id,
    }


def _task_from_dict(d: dict[str, object]) -> dict[str, object]:
    """Materialize a Task-row dict back into kwargs suitable for ``Task(**)`` / UPSERT.

    Inverse of :func:`_task_to_dict`. Datetime fields are parsed to UTC-aware
    instances via :func:`_datetime_from_iso`. Datetimes are guaranteed
    non-null on Task rows, so a missing value raises ``ValueError`` (not an
    ``assert`` — those vanish under ``python -O``).
    """
    created_at = _datetime_from_iso(_str_field(d, "created_at"))
    updated_at = _datetime_from_iso(_str_field(d, "updated_at"))
    if created_at is None:
        raise ValueError(f"task {d.get('id', '?')!r}: created_at must not be null in v1 payload")
    if updated_at is None:
        raise ValueError(f"task {d.get('id', '?')!r}: updated_at must not be null in v1 payload")
    return {
        "id": _str_field(d, "id"),
        "status": _str_field(d, "status"),
        "created_at": created_at,
        "updated_at": updated_at,
        "actor_kind": _str_field(d, "actor_kind"),
        "actor_id": _str_field(d, "actor_id"),
        "title": _opt_str_field(d, "title"),
        "last_event_id": _opt_str_field(d, "last_event_id"),
    }


def _session_to_dict(s: SessionRow) -> dict[str, object]:
    """Serialize a SessionRow ORM row to a JSON-safe dict."""
    return {
        "id": s.id,
        "task_id": s.task_id,
        "worker_kind": s.worker_kind,
        "worktree_path": s.worktree_path,
        "status": s.status,
        "started_at": _datetime_to_iso(s.started_at),
        "ended_at": _datetime_to_iso(s.ended_at),
        "last_heartbeat_at": _datetime_to_iso(s.last_heartbeat_at),
    }


def _session_from_dict(d: dict[str, object]) -> dict[str, object]:
    """Materialize a SessionRow-row dict back into kwargs for UPSERT."""
    started_at = _datetime_from_iso(_str_field(d, "started_at"))
    if started_at is None:
        raise ValueError(f"session {d.get('id', '?')!r}: started_at must not be null in v1 payload")
    return {
        "id": _str_field(d, "id"),
        "task_id": _str_field(d, "task_id"),
        "worker_kind": _str_field(d, "worker_kind"),
        "worktree_path": _opt_str_field(d, "worktree_path"),
        "status": _str_field(d, "status"),
        "started_at": started_at,
        "ended_at": _datetime_from_iso(_opt_str_field(d, "ended_at")),
        "last_heartbeat_at": _datetime_from_iso(_opt_str_field(d, "last_heartbeat_at")),
    }


class SnapshotPolicy:
    """Periodic-snapshot capture policy.

    Wire one instance into the subscriber loop and call
    :meth:`maybe_capture` after every successful ``apply_many``. Once the
    tally of applied events meets or exceeds ``interval`` the next call
    produces a snapshot and resets the tally to 0. :meth:`capture` is the
    force-now escape hatch — it ignores the tally entirely.

    The internal ``asyncio.Lock`` makes the class safe-by-construction even
    if (in some future world) two coroutines call ``maybe_capture``
    concurrently. FR26 (single subscriber, enforced by
    ``scripts/check_single_writer.py``) makes this academic in production,
    but the lock is cheap insurance.
    """

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[AsyncSession],
        clock: Clock,
        interval: int = 1000,
    ) -> None:
        if interval <= 0:
            raise ValueError(f"interval must be positive; got {interval}")
        if interval == 1:
            log.warning(
                "SnapshotPolicy interval=1 produces one snapshot per event — "
                "performance hazard outside tests"
            )
        self._session_maker = session_maker
        self._clock = clock
        self._interval = interval
        self._events_since_snapshot = 0
        # Constructed eagerly: Python 3.12 binds the lock to the running
        # event loop on first acquire, so building it outside an event loop
        # is safe (mirrors Story 2.5's writer pattern).
        self._lock = asyncio.Lock()

    async def maybe_capture(self, last_envelope: EventEnvelope, applied_count: int) -> str | None:
        """Increment the tally; capture AT MOST ONCE per call when threshold crossed.

        Cap-at-1 semantics: even when ``applied_count`` is large enough to
        cross several interval boundaries (e.g. interval=10 and applied_count=25),
        this method takes EXACTLY ONE snapshot. The snapshot represents the
        end-of-batch materialized state — anchored on ``last_envelope`` — and
        the residual modulo (``25 % 10 == 5``) is rolled forward into
        ``_events_since_snapshot`` so the next batch's tally remains accurate.

        Why cap-at-1: a single ``apply_many`` only knows the highest envelope
        applied, so taking N snapshots in a loop would write N identical rows
        with the same cursor. One snapshot per call is both correct and
        cheaper.

        Args:
            last_envelope: The highest-monotonic_ns envelope that was just
                applied — used as the snapshot's cursor anchor.
            applied_count: Number of *new* events applied in the just-finished
                ``apply_many`` batch. Duplicates do NOT count. Negative values
                raise ``ValueError`` since they would silently rewind the
                tally and skip a future snapshot.

        Returns:
            The id of the captured snapshot if one was written, else ``None``.
        """
        if applied_count < 0:
            raise ValueError(f"applied_count must be non-negative; got {applied_count}")
        async with self._lock:
            self._events_since_snapshot += applied_count
            if self._events_since_snapshot < self._interval:
                return None
            snapshot_id = await self._capture(last_envelope)
            # Roll forward any overflow so accumulated batches don't lose
            # progress. Using `%` rather than `-=` makes batches that span
            # multiple intervals correct in one operation.
            self._events_since_snapshot %= self._interval
            return snapshot_id

    async def capture(self, last_envelope: EventEnvelope) -> str:
        """Force-capture a snapshot now, ignoring the tally. Returns the new snapshot id."""
        async with self._lock:
            return await self._capture(last_envelope)

    async def _capture(self, last_envelope: EventEnvelope) -> str:
        """Atomic capture: read state + count events + INSERT row, all in one txn.

        See module docstring for the atomicity contract. The payload
        serializes via ``json.dumps(sort_keys=True, ...)`` to guarantee
        byte-stable output across runs — this is what AC-8's
        full-replay-vs-snapshot-replay byte-for-byte equivalence test relies
        on.
        """
        async with self._session_maker() as session, session.begin():
            tasks = (await session.execute(select(Task))).scalars().all()
            session_rows = (await session.execute(select(SessionRow))).scalars().all()
            # Restrict the count to events the snapshot's cursor covers — the
            # snapshot represents state at ``last_envelope.emitted_at_monotonic_ns``,
            # so post-cursor rows that may have raced into the events table on
            # parallel writers (or arrived between the read and the count under
            # a less-strict isolation level) are excluded.
            event_count = (
                await session.execute(
                    select(func.count())
                    .select_from(Event)
                    .where(Event.emitted_at_monotonic_ns <= last_envelope.emitted_at_monotonic_ns)
                )
            ).scalar_one()
            payload: dict[str, object] = {
                "version": 1,
                "tasks": [_task_to_dict(t) for t in tasks],
                "sessions": [_session_to_dict(s) for s in session_rows],
                "cursor_emitted_at_monotonic_ns": last_envelope.emitted_at_monotonic_ns,
            }
            payload_json = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=_default_encoder,
            )
            payload_bytes = payload_json.encode("utf-8")
            snapshot_id = new_uuid7(clock=self._clock)
            now = self._clock.now()
            session.add(
                Snapshot(
                    id=snapshot_id,
                    created_at=now,
                    cursor_event_id=last_envelope.event_id,
                    event_count=event_count,
                    byte_size=len(payload_bytes),
                    payload_json=payload_json,
                )
            )
            return snapshot_id


__all__ = ["SnapshotPolicy"]
