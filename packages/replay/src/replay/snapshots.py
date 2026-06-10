"""Snapshot management for fast replay restoration (Phase 12 / Story 62-2).

Snapshots capture the materialized state at a given event-log position so that
subsequent replays can skip already-processed events and start from the
snapshot state instead of replaying from the beginning.

Snapshot files are stored as ``{snapshot_dir}/{snapshot_id}.json`` —
human-readable, pretty-printed JSON with deterministic key ordering.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SnapshotInfo:
    """Metadata and payload of a single snapshot."""

    snapshot_id: str  # UUID
    sequence_number: int  # the event log position at snapshot time
    timestamp: str  # ISO 8601 when snapshot was taken
    state: dict[str, Any]  # the materialized state
    size_bytes: int  # approximate size of the snapshot JSON


def create_snapshot(
    *,
    event_log_dir: Path,
    snapshot_dir: Path,
) -> SnapshotInfo:
    """Replay to the latest event and persist the materialized state.

    1. Calls :func:`replay.engine.replay_events` with ``up_to=sys.maxsize``
       to materialize the full current state.
    2. Writes a pretty-printed JSON file to ``snapshot_dir/{snapshot_id}.json``.
    3. Returns a :class:`SnapshotInfo` describing the snapshot.

    This is a **synchronous** helper — the caller is responsible for running
    it in a thread if the surrounding context is async (the replay engine
    itself is async).
    """
    import asyncio

    from replay.engine import replay_events

    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Run the async replay engine. create_snapshot is sync so that callers
    # can use it from thread workers; we create a throw-away event loop.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    async def _do_replay() -> Any:
        return await replay_events(
            up_to=sys.maxsize,
            event_log_dir=event_log_dir,
        )

    if loop is not None and loop.is_running():
        # We are inside an existing event loop — run in a new thread to
        # avoid "cannot run the event loop while another loop is running".
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(asyncio.run, _do_replay()).result()
    else:
        result = asyncio.run(_do_replay())

    snapshot_id = uuid4().hex
    timestamp = datetime.now(UTC).isoformat()
    seq_end = result.metadata.sequence_end if result.metadata.event_count > 0 else 0

    snapshot_data = {
        "snapshot_id": snapshot_id,
        "sequence_number": seq_end,
        "timestamp": timestamp,
        "state": result.state,
    }

    raw = json.dumps(snapshot_data, indent=2, sort_keys=True)
    size_bytes = len(raw.encode("utf-8"))

    path = snapshot_dir / f"{snapshot_id}.json"
    path.write_text(raw, encoding="utf-8")

    _log.info(
        "snapshot_created",
        snapshot_id=snapshot_id,
        sequence_number=seq_end,
        size_bytes=size_bytes,
    )

    return SnapshotInfo(
        snapshot_id=snapshot_id,
        sequence_number=seq_end,
        timestamp=timestamp,
        state=result.state,
        size_bytes=size_bytes,
    )


def list_snapshots(
    *,
    snapshot_dir: Path,
) -> list[SnapshotInfo]:
    """Read all snapshot files and return them sorted by ``sequence_number``."""
    if not snapshot_dir.is_dir():
        return []

    snapshots: list[SnapshotInfo] = []
    for path in sorted(snapshot_dir.glob("*.json")):
        info = _load_snapshot_file(path)
        if info is not None:
            snapshots.append(info)

    snapshots.sort(key=lambda s: s.sequence_number)
    return snapshots


def load_snapshot(
    *,
    snapshot_id: str,
    snapshot_dir: Path,
) -> SnapshotInfo | None:
    """Load a specific snapshot by ID."""
    path = snapshot_dir / f"{snapshot_id}.json"
    if not path.is_file():
        return None
    return _load_snapshot_file(path)


def find_nearest_snapshot(
    *,
    target_sequence: int,
    snapshot_dir: Path,
) -> SnapshotInfo | None:
    """Find the most recent snapshot whose ``sequence_number`` < *target_sequence*.

    Returns ``None`` if no suitable snapshot exists.
    """
    all_snapshots = list_snapshots(snapshot_dir=snapshot_dir)
    # Filter to snapshots strictly before the target
    candidates = [s for s in all_snapshots if s.sequence_number < target_sequence]
    if not candidates:
        return None
    # Return the one with the highest sequence number (nearest)
    return max(candidates, key=lambda s: s.sequence_number)


def _load_snapshot_file(path: Path) -> SnapshotInfo | None:
    """Load and validate a single snapshot JSON file."""
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return SnapshotInfo(
            snapshot_id=data["snapshot_id"],
            sequence_number=data["sequence_number"],
            timestamp=data["timestamp"],
            state=data["state"],
            size_bytes=len(raw.encode("utf-8")),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        _log.warning("snapshot_load_failed", path=str(path))
        return None


__all__ = [
    "SnapshotInfo",
    "create_snapshot",
    "find_nearest_snapshot",
    "list_snapshots",
    "load_snapshot",
]
