"""Package-only streaming replay API (Phase 13 / P13-ELLM)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

from replay.archive_manifest import ArchiveManifestInput
from replay.engine import DEFAULT_BATCH_SIZE, DEFAULT_MEMORY_LIMIT_BYTES, replay_events_iter
from replay.models import ReplayProgress, ReplayResult


async def replay_events_stream(
    *,
    up_to: datetime | int,
    event_log_dir: Path,
    batch_size: int = DEFAULT_BATCH_SIZE,
    memory_limit_bytes: int = DEFAULT_MEMORY_LIMIT_BYTES,
    snapshot_dir: Path | None = None,
    archive_manifest_path: ArchiveManifestInput = None,
) -> AsyncIterator[ReplayProgress | ReplayResult]:
    """Replay events and yield batch progress followed by the final result."""
    async for item in replay_events_iter(
        up_to=up_to,
        event_log_dir=event_log_dir,
        batch_size=batch_size,
        memory_limit_bytes=memory_limit_bytes,
        snapshot_dir=snapshot_dir,
        archive_manifest_path=archive_manifest_path,
        emit_progress=True,
    ):
        yield item


__all__ = ["ReplayProgress", "replay_events_stream"]
