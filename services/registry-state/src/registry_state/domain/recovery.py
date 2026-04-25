"""Snapshot-aware startup recovery — restore + replay-cursor (Story 2.6).

============================================================================
HIGH-RISK FILE — Architecture §line-834 / §line-1055.
----------------------------------------------------------------------------
Pair-review + explicit test coverage are MANDATORY for any change to this
module. The risk is not algorithmic complexity — it's that a bug here can
silently desync the materialized state from the event log: replay starts
from the wrong cursor, snapshot UPSERT writes the wrong row, or a future
schema-version dispatch swallows an unsupported payload.

If you are an operator reading this in production after a recovery anomaly:
the event log under ``REGISTRY_STATE_LOG_DIR`` is the source of truth.
Snapshots are checkpoints, NOT data. ``DROP TABLE snapshots;`` followed by
a process restart triggers a full replay-from-zero — slow but always
correct (provided the JSONL log is intact).
============================================================================

Three free functions, called sequentially at subscriber startup:

1. :func:`restore_state_from_latest_snapshot` —
   Loads the latest snapshot row, parses its v1 payload, UPSERTs every
   task + session into the live tables. Returns the snapshot's cursor
   (``cursor_emitted_at_monotonic_ns``). If no snapshot exists, returns 0
   and leaves both tables untouched.

2. :func:`compute_events_max_cursor` —
   Returns ``MAX(events.emitted_at_monotonic_ns)`` (or 0 if the events table
   is empty). Caller composes ``max(restored_cursor, events_max)`` to choose
   the replay anchor without re-parsing the snapshot's payload JSON twice.

3. :func:`compute_replay_cursor` —
   Convenience wrapper — re-reads the latest snapshot AND the events-max
   and returns ``max(snapshot_cursor, MAX(events.emitted_at_monotonic_ns))``.
   Kept for callers that want a single-shot cursor without first invoking
   ``restore_state_from_latest_snapshot``.

Idempotency-by-construction
---------------------------
Restore always upserts. Even if the events table contains rows past the
snapshot's cursor (the AC-12 "stale snapshot + newer events" case), the
post-snapshot replay's existing ON-CONFLICT-DO-UPDATE handlers will
re-overwrite any task/session row the snapshot stamped with stale state.
The final state matches a full replay-from-zero exactly — that's AC-8's
byte-for-byte equivalence test.

Preserved columns
-----------------
The UPSERT's ``DO UPDATE SET`` clause excludes both ``id`` (the conflict
key) and ``created_at`` — the latter so a stale snapshot can never overwrite
the original insertion timestamp on rows that the live DB has already
updated past the snapshot's view. Snapshots are checkpoints, not history;
``created_at`` is history.

Schema-version dispatch
-----------------------
Story 2.6 ships v1 only. Future versions add branches; an unknown version
raises ``ValueError`` so the subscriber crashes loudly rather than
applying a half-understood payload.
"""

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from registry_state.domain.snapshots import (
    _session_from_dict,
    _task_from_dict,
)
from registry_state.schema import Event, Snapshot, Task
from registry_state.schema import Session as SessionRow


async def restore_state_from_latest_snapshot(
    session_maker: async_sessionmaker[AsyncSession],
) -> int:
    """Upsert tasks + sessions from the latest snapshot; return its cursor (or 0 if none).

    HIGH-RISK file (Arch §line-834). Pair-review + explicit test coverage required.

    Behaviour:

    - No snapshot present → returns 0; leaves all tables untouched.
    - Snapshot present → UPSERTs every task + session row from the
      payload via ``ON CONFLICT DO UPDATE`` (idempotent — see module
      docstring's "idempotency-by-construction" section).
    - Snapshot session FK references a task NOT in the same snapshot's
      tasks list → raises ``ValueError`` (corrupt snapshot). This catches
      payloads that violate intra-snapshot consistency. A live-DB task that
      the snapshot's session references but the snapshot's tasks list omits
      is still fine — the upsert path leaves the live row alone.
    - Unknown payload version → raises ``ValueError``. The subscriber
      catches no exceptions here, so the process exits → Docker / systemd
      restarts → eventually full-replay-from-zero takes over once an
      operator removes / fixes the offending snapshot row.

    The whole restore runs inside one transaction so a partial UPSERT is
    impossible.

    Latest-snapshot tiebreak: when two snapshot rows share the same
    ``created_at`` (rare but possible under coarse-resolution clocks),
    ``id DESC`` chooses the one with the highest UUIDv7 — which is also
    monotonically the one inserted last.

    Preserved columns: ``id`` (conflict key) AND ``created_at`` are excluded
    from the UPSERT's ``DO UPDATE SET``. See module docstring's "Preserved
    columns" section.
    """
    async with session_maker() as session, session.begin():
        latest_stmt = (
            select(Snapshot).order_by(Snapshot.created_at.desc(), Snapshot.id.desc()).limit(1)
        )
        latest = (await session.execute(latest_stmt)).scalar_one_or_none()
        if latest is None:
            return 0
        payload = json.loads(latest.payload_json)
        version = payload.get("version")
        if version != 1:
            raise ValueError(f"unsupported snapshot payload version: {version!r}")
        # Intra-snapshot FK validation: every session must reference a task
        # that is ALSO in this same snapshot. A live-DB task the snapshot
        # omits would still satisfy the upsert (we never delete tasks during
        # restore) — only payloads that are internally inconsistent fail
        # here.
        task_ids_in_payload = {t["id"] for t in payload["tasks"]}
        for sess in payload["sessions"]:
            if sess["task_id"] not in task_ids_in_payload:
                raise ValueError(
                    f"corrupt snapshot {latest.id}: session {sess['id']} "
                    f"references missing task {sess['task_id']}"
                )
        for task_dict in payload["tasks"]:
            row = _task_from_dict(task_dict)
            # Exclude ``created_at`` so a stale snapshot can never regress
            # the insertion timestamp of a row the live DB has already
            # touched. ``id`` is excluded because it's the conflict key.
            update_set = {k: v for k, v in row.items() if k not in ("id", "created_at")}
            stmt_t = (
                sqlite_insert(Task)
                .values(**row)
                .on_conflict_do_update(index_elements=["id"], set_=update_set)
            )
            await session.execute(stmt_t)
        for sess_dict in payload["sessions"]:
            row_s = _session_from_dict(sess_dict)
            # Sessions don't have a ``created_at`` column today, so the
            # ("id", "created_at") filter is identical to the tasks loop's
            # — kept symmetric so future sessions.created_at additions don't
            # silently start regressing.
            update_set_s = {k: v for k, v in row_s.items() if k not in ("id", "created_at")}
            stmt_s = (
                sqlite_insert(SessionRow)
                .values(**row_s)
                .on_conflict_do_update(index_elements=["id"], set_=update_set_s)
            )
            await session.execute(stmt_s)
        return int(payload["cursor_emitted_at_monotonic_ns"])


async def compute_events_max_cursor(
    session_maker: async_sessionmaker[AsyncSession],
) -> int:
    """Return ``MAX(events.emitted_at_monotonic_ns)``, or 0 if the events table is empty.

    Split out from :func:`compute_replay_cursor` so the subscriber's startup
    loop can reuse the cursor returned by
    :func:`restore_state_from_latest_snapshot` without re-parsing the
    snapshot's ``payload_json`` a second time. Composition lives at the
    call site::

        restored = await restore_state_from_latest_snapshot(sm)
        events_max = await compute_events_max_cursor(sm)
        cursor = max(restored, events_max)
    """
    async with session_maker() as session:
        events_max_stmt = select(func.max(Event.emitted_at_monotonic_ns))
        events_cursor_raw: int | None = (await session.execute(events_max_stmt)).scalar()
        return int(events_cursor_raw) if events_cursor_raw is not None else 0


async def compute_replay_cursor(
    session_maker: async_sessionmaker[AsyncSession],
) -> int:
    """Return ``max(snapshot_cursor, MAX(events.emitted_at_monotonic_ns))``, or 0 if both empty.

    Used by callers that want a one-shot cursor without first invoking
    :func:`restore_state_from_latest_snapshot`. The subscriber's startup
    loop prefers ``max(restored_cursor, compute_events_max_cursor(sm))`` to
    avoid re-parsing the snapshot's payload twice — see Story 2.6 F9.

    Edge cases:

    - Both empty → 0 (full replay).
    - Snapshot only (events wiped, e.g. after ``DROP TABLE events;``) →
      snapshot's cursor.
    - Events only (snapshot deleted) → ``MAX(events.emitted_at_monotonic_ns)``.
    - Both present → the higher value, so neither anchor is regressed past.

    Latest-snapshot tiebreak: matches
    :func:`restore_state_from_latest_snapshot` — ``created_at DESC, id DESC``
    so both functions agree on "the latest" under coarse clocks.
    """
    async with session_maker() as session:
        snapshot_stmt = (
            select(Snapshot).order_by(Snapshot.created_at.desc(), Snapshot.id.desc()).limit(1)
        )
        latest = (await session.execute(snapshot_stmt)).scalar_one_or_none()
        if latest is not None:
            payload = json.loads(latest.payload_json)
            snapshot_cursor = int(payload["cursor_emitted_at_monotonic_ns"])
        else:
            snapshot_cursor = 0
        events_max_stmt = select(func.max(Event.emitted_at_monotonic_ns))
        events_cursor_raw: int | None = (await session.execute(events_max_stmt)).scalar()
        events_cursor = int(events_cursor_raw) if events_cursor_raw is not None else 0
        return max(snapshot_cursor, events_cursor)


__all__ = [
    "compute_events_max_cursor",
    "compute_replay_cursor",
    "restore_state_from_latest_snapshot",
]
