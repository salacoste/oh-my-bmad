"""Core replay engine — point-in-time state reconstruction from the JSONL event log.

Phase 12 / Story 60-1 (ADR-0024).

Reads all JSONL event-log files in chronological order, replays them through
the same Materializer + handlers used by the live subscriber, and returns the
materialized state as a :class:`ReplayResult`.

Key invariants (from PRD):
  * **P12-I1**: Replay is READ-ONLY. Never writes to the live database.
  * **P12-I2**: Events processed in ``emitted_at_monotonic_ns`` order.
  * **NFR-R17**: Memory bounded — configurable batch size (default 500),
    256 MB limit, :class:`ReplayMemoryError` on breach.
  * **NFR-M12**: Read-only guarantee enforced at engine level (in-memory DB).
  * **NFR-O21**: 10 K events in < 5 seconds target.
"""

from __future__ import annotations

import resource
import sys
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from events.envelope import EventEnvelope
from events.log_reader import read_log_lines
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from replay.models import ReplayMemoryError, ReplayMetadata, ReplayResult

_log = structlog.get_logger(__name__)

# Default memory limit: 256 MB (NFR-R17)
_DEFAULT_MEMORY_LIMIT_BYTES = 256 * 1024 * 1024

# Default batch size for memory-checked replay (NFR-R17)
_DEFAULT_BATCH_SIZE = 500


def _check_memory(limit_bytes: int) -> None:
    """Check current process RSS against *limit_bytes*.

    Raises :class:`ReplayMemoryError` if the resident set size exceeds the
    configured limit.

    macOS reports ``ru_maxrss`` in bytes; Linux in kilobytes — normalised.
    """
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "linux":
        rss *= 1024
    if rss > limit_bytes:
        raise ReplayMemoryError(current_bytes=rss, limit_bytes=limit_bytes)


def _collect_envelopes(event_log_dir: Path) -> list[EventEnvelope]:
    """Read all JSONL files from *event_log_dir*, sorted by filename.

    Returns envelopes sorted by ``emitted_at_monotonic_ns`` (P12-I2).
    """
    envelopes: list[EventEnvelope] = []
    jsonl_files = sorted(event_log_dir.glob("*.jsonl"))
    for path in jsonl_files:
        for env in read_log_lines(path):
            envelopes.append(env)
    # Sort by monotonic sequence number (P12-I2)
    envelopes.sort(key=lambda e: e.emitted_at_monotonic_ns)
    return envelopes


def _filter_envelopes(
    envelopes: Sequence[EventEnvelope],
    up_to: datetime | int,
) -> list[EventEnvelope]:
    """Filter envelopes to those <= *up_to* (by timestamp or sequence number)."""
    if isinstance(up_to, int):
        return [e for e in envelopes if e.emitted_at_monotonic_ns <= up_to]
    # datetime → compare against emitted_at (UTC-aware)
    target_ts = up_to.timestamp()
    return [e for e in envelopes if e.emitted_at.timestamp() <= target_ts]


async def replay_events(
    *,
    up_to: datetime | int,
    event_log_dir: Path,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    memory_limit_bytes: int = _DEFAULT_MEMORY_LIMIT_BYTES,
    snapshot_dir: Path | None = None,
) -> ReplayResult:
    """Replay historical events and return the materialized point-in-time state.

    Creates an in-memory SQLite database, applies events through the same
    Materializer + handlers used by the live subscriber, and returns the
    final state as a :class:`ReplayResult`.

    When *snapshot_dir* is provided and a suitable snapshot exists before the
    replay target, the engine loads the snapshot state and only replays events
    after the snapshot position — significantly reducing work for large logs.

    The in-memory DB is discarded after the call — no write-path side effects
    on the live database (P12-I1, NFR-M12).

    Args:
        up_to: Replay target — either a UTC :class:`datetime` or an
            ``emitted_at_monotonic_ns`` integer. Events after this point
            are excluded.
        event_log_dir: Directory containing ``YYYY-MM-DD.jsonl`` files.
        batch_size: Number of events per memory-check batch (default 500).
        memory_limit_bytes: RSS limit in bytes (default 256 MB).
        snapshot_dir: Optional directory containing snapshot JSON files.
            When provided, the engine will attempt to find and use the
            nearest snapshot before *up_to* to skip already-replayed events.

    Returns:
        :class:`ReplayResult` with materialized state and replay metadata.

    Raises:
        FileNotFoundError: If *event_log_dir* does not exist.
        ReplayMemoryError: If RSS exceeds *memory_limit_bytes* during replay.
    """
    # Lazy imports to keep the module-level import graph light — the replay
    # package is consumed by services that already depend on registry-state.
    from registry_state.domain.handlers import (  # noqa: IMP001 — replay re-uses the canonical materializer (ADR-0024 D3)
        register_default_handlers,
    )
    from registry_state.domain.materializer import (  # noqa: IMP001 — same materializer as registry-state startup (FR134)
        Materializer,
    )
    from registry_state.schema import (  # noqa: IMP001 — in-memory SQLAlchemy Base for ephemeral replay DB (P12-I1)
        Base,
    )

    start_wall = time.monotonic()

    # Snapshot optimization (Story 62-2): if a snapshot_dir is provided and
    # the target is an integer sequence number, attempt to find the nearest
    # snapshot before the target and replay only the delta.
    snapshot_source_id: str | None = None
    initial_state: dict[str, Any] | None = None
    skip_before: int = 0  # skip envelopes with seq <= this value

    if snapshot_dir is not None and isinstance(up_to, int):
        from replay.snapshots import find_nearest_snapshot
        from replay.snapshots import load_snapshot as _load_snap

        nearest = find_nearest_snapshot(
            target_sequence=up_to,
            snapshot_dir=snapshot_dir,
        )
        if nearest is not None:
            loaded = _load_snap(
                snapshot_id=nearest.snapshot_id,
                snapshot_dir=snapshot_dir,
            )
            if loaded is not None:
                snapshot_source_id = loaded.snapshot_id
                initial_state = loaded.state
                skip_before = loaded.sequence_number
                _log.info(
                    "replay_using_snapshot",
                    snapshot_id=snapshot_source_id,
                    snapshot_seq=skip_before,
                    target_seq=up_to,
                )

    # 1. Collect and filter envelopes
    all_envelopes = _collect_envelopes(event_log_dir)
    envelopes = _filter_envelopes(all_envelopes, up_to)

    # If using a snapshot, skip envelopes at or before the snapshot position
    if skip_before > 0:
        envelopes = [e for e in envelopes if e.emitted_at_monotonic_ns > skip_before]

    if not envelopes and initial_state is not None:
        # Snapshot already covers the target — return snapshot state directly
        elapsed = time.monotonic() - start_wall
        return ReplayResult(
            state=initial_state,
            metadata=ReplayMetadata(
                event_count=0,
                sequence_start=skip_before,
                sequence_end=skip_before,
                replay_duration_s=elapsed,
                snapshot_source=snapshot_source_id,
            ),
        )

    if not envelopes:
        return ReplayResult(
            state={"tasks": [], "sessions": []},
            metadata=ReplayMetadata(
                event_count=0,
                sequence_start=0,
                sequence_end=0,
                replay_duration_s=time.monotonic() - start_wall,
                snapshot_source=None,
            ),
        )

    seq_start = envelopes[0].emitted_at_monotonic_ns
    seq_end = envelopes[-1].emitted_at_monotonic_ns

    # 2. In-memory SQLite (P12-I1 / NFR-M12: never touches the live DB)
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_maker = async_sessionmaker(engine, class_=AsyncSession)

        # 3. Materializer + handlers (same dispatch as the live subscriber)
        materializer = Materializer(session_maker=session_maker)
        register_default_handlers(materializer)

        # 3a. If a snapshot provides initial state, seed the in-memory DB
        if initial_state is not None:
            await _seed_state(session_maker, initial_state)

        # 4. Apply events in batches with memory checking (NFR-R17)
        for batch_start in range(0, len(envelopes), batch_size):
            batch = envelopes[batch_start : batch_start + batch_size]
            await materializer.apply_many(batch)
            _check_memory(memory_limit_bytes)

        # 5. Read final state from in-memory DB
        state = await _read_state(session_maker)

        elapsed = time.monotonic() - start_wall
        _log.info(
            "replay_complete",
            event_count=len(envelopes),
            duration_s=round(elapsed, 3),
            snapshot_source=snapshot_source_id,
        )

        return ReplayResult(
            state=state,
            metadata=ReplayMetadata(
                event_count=len(envelopes),
                sequence_start=seq_start,
                sequence_end=seq_end,
                replay_duration_s=elapsed,
                snapshot_source=snapshot_source_id,
            ),
        )
    finally:
        await engine.dispose()


async def _seed_state(
    session_maker: async_sessionmaker[AsyncSession],
    state: dict[str, Any],
) -> None:
    """Seed the in-memory DB with a snapshot's materialized state."""
    from datetime import datetime as _dt

    from registry_state.schema import (  # noqa: IMP001 — seeding in-memory ephemeral DB (P12-I1)
        Session as SessionRow,
    )
    from registry_state.schema import (  # noqa: IMP001 — seeding in-memory ephemeral DB (P12-I1)
        Task,
    )

    async with session_maker() as session:
        for t_data in state.get("tasks", []):
            created_at = t_data.get("created_at")
            updated_at = t_data.get("updated_at")
            task = Task(
                id=t_data["id"],
                status=t_data.get("status", "pending"),
                title=t_data.get("title", ""),
                created_at=_dt.fromisoformat(created_at) if created_at else None,
                updated_at=_dt.fromisoformat(updated_at) if updated_at else None,
                actor_kind=t_data.get("actor_kind", "operator"),
                actor_id=t_data.get("actor_id", ""),
                worker_id=t_data.get("worker_id"),
                hint=t_data.get("hint"),
                total_steps=t_data.get("total_steps", 0),
                current_step=t_data.get("current_step", 0),
                last_agent_action=t_data.get("last_agent_action"),
                retry_count=t_data.get("retry_count", 0),
                blocker_reason=t_data.get("blocker_reason"),
                chat_id=t_data.get("chat_id"),
                reply_to_message_id=t_data.get("reply_to_message_id"),
                budget_token_limit=t_data.get("budget_token_limit"),
                budget_action=t_data.get("budget_action"),
            )
            session.add(task)  # noqa: SW001 — seeding in-memory ephemeral DB for replay snapshot (P12-I1)

        for s_data in state.get("sessions", []):
            started_at = s_data.get("started_at")
            ended_at = s_data.get("ended_at")
            last_heartbeat_at = s_data.get("last_heartbeat_at")
            sess = SessionRow(
                id=s_data["id"],
                task_id=s_data["task_id"],
                worker_kind=s_data.get("worker_kind", ""),
                worktree_path=s_data.get("worktree_path"),
                status=s_data.get("status", "active"),
                started_at=_dt.fromisoformat(started_at) if started_at else None,
                ended_at=_dt.fromisoformat(ended_at) if ended_at else None,
                last_heartbeat_at=(
                    _dt.fromisoformat(last_heartbeat_at) if last_heartbeat_at else None
                ),
            )
            session.add(sess)  # noqa: SW001 — seeding in-memory ephemeral DB for replay snapshot (P12-I1)

        await session.commit()


async def _read_state(
    session_maker: async_sessionmaker[AsyncSession],
) -> dict[str, list[dict[str, Any]]]:
    """Read all tasks and sessions from the in-memory database."""
    from registry_state.schema import (  # noqa: IMP001 — reading from in-memory replay DB (ADR-0024 D3)
        Session as SessionRow,
    )
    from registry_state.schema import (  # noqa: IMP001 — reading from in-memory replay DB (ADR-0024 D3)
        Task,
    )

    async with session_maker() as session:
        # Tasks
        result = await session.execute(select(Task))
        tasks = result.scalars().all()
        task_dicts = [
            {
                "id": t.id,
                "status": t.status,
                "title": t.title,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                "actor_kind": t.actor_kind,
                "actor_id": t.actor_id,
                "worker_id": t.worker_id,
                "hint": t.hint,
                "total_steps": t.total_steps,
                "current_step": t.current_step,
                "last_agent_action": t.last_agent_action,
                "retry_count": t.retry_count,
                "blocker_reason": t.blocker_reason,
                "chat_id": t.chat_id,
                "reply_to_message_id": t.reply_to_message_id,
                "budget_token_limit": t.budget_token_limit,
                "budget_action": t.budget_action,
            }
            for t in tasks
        ]

        # Sessions
        session_result = await session.execute(select(SessionRow))
        sessions = session_result.scalars().all()
        session_dicts = [
            {
                "id": s.id,
                "task_id": s.task_id,
                "worker_kind": s.worker_kind,
                "worktree_path": s.worktree_path,
                "status": s.status,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "last_heartbeat_at": (
                    s.last_heartbeat_at.isoformat() if s.last_heartbeat_at else None
                ),
            }
            for s in sessions
        ]

    return {"tasks": task_dicts, "sessions": session_dicts}


__all__ = ["replay_events"]
