"""GET /v1/events/replay + GET /v1/tasks/{task_id}/history route handlers

(Phase 12 / Stories 61-1 and 61-2 / ADR-0024).

Two read-only endpoints that operate on the append-only JSONL event log:

  * ``GET /v1/events/replay`` — point-in-time state reconstruction via the
    replay engine (Story 61-1).
  * ``GET /v1/tasks/{task_id}/history`` — event-history for a single task,
    read directly from JSONL without materialization (Story 61-2).

Auth is middleware-based (JwtAuthMiddleware). The actor_id is available via
``request.state.actor_id`` after auth.

Audit logging (NFR-S17): both endpoints log every operation via structlog.
"""

from __future__ import annotations

import asyncio
import pathlib
from datetime import datetime
from typing import Any, cast

import structlog
from events.log_reader import read_log_lines
from fastapi import APIRouter, Path, Query, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from registry_state.schema import (  # noqa: IMP001 — registry-api reads registry-state models for task history query (FR136)
    Session as SessionRow,
)
from registry_state.schema import (  # noqa: IMP001 — registry-api reads registry-state models for task history query (FR136)
    Task,
)
from replay import replay_events
from replay.archive_manifest import resolve_archive_manifest_path
from replay.errors import (
    ReplayArchiveChecksumError,
    ReplayArchiveConfigError,
    ReplayArchiveConflictError,
    ReplayArchiveError,
    ReplayArchiveManifestError,
    ReplayArchiveMissingSegmentError,
)
from replay.snapshots import create_snapshot as _create_snapshot
from replay.snapshots import list_snapshots as _list_snapshots
from replay.validation import validate_replay
from sqlalchemy import select

from registry_api.adapters.errors import ProblemDetails
from registry_api.routes.tasks import _TASK_ID_PATTERN

_log = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class ReplayResponse(BaseModel):
    """Response body for GET /v1/events/replay."""

    model_config = ConfigDict(frozen=True, strict=True)

    state: dict[str, Any]  # materialized tasks + sessions
    event_count: int
    sequence_start: int
    sequence_end: int
    replay_duration_s: float


class TaskHistoryEntry(BaseModel):
    """Single event in a task's history."""

    model_config = ConfigDict(frozen=True, strict=True)

    sequence_number: int
    emitted_at: str  # ISO 8601
    event_type: str
    actor_kind: str
    actor_id: str
    trace_id: str
    payload_summary: dict[str, Any]  # key fields from payload


class TaskHistoryResponse(BaseModel):
    """Response body for GET /v1/tasks/{task_id}/history."""

    model_config = ConfigDict(frozen=True, strict=True)

    events: list[TaskHistoryEntry]
    total: int
    limit: int
    offset: int


class ValidationFieldDiffResponse(BaseModel):
    """Single field-level mismatch between replayed and live state."""

    model_config = ConfigDict(frozen=True, strict=True)

    table: str
    row_id: str
    field: str
    expected: Any
    actual: Any


class ValidateReplayResponse(BaseModel):
    """Response body for GET /v1/events/replay/validate (Story 62-1)."""

    model_config = ConfigDict(frozen=True, strict=True)

    total_fields: int
    matching_fields: int
    mismatching_fields: int
    diffs: list[ValidationFieldDiffResponse]


class SnapshotEntryResponse(BaseModel):
    """Single snapshot entry in list/create responses (Story 62-2)."""

    model_config = ConfigDict(frozen=True, strict=True)

    snapshot_id: str
    sequence_number: int
    timestamp: str
    size_bytes: int


class SnapshotListResponse(BaseModel):
    """Response body for GET /v1/events/replay/snapshots (Story 62-2)."""

    model_config = ConfigDict(frozen=True, strict=True)

    snapshots: list[SnapshotEntryResponse]
    total: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event_log_dir(request: Request) -> pathlib.Path:
    """Resolve the event log directory from app.state or environment.

    ``build_app`` stores the ``EventLogWriter`` on ``app.state.writer``;
    the writer's private ``_base_dir`` attribute holds the log directory.
    Falls back to the ``EVENT_LOG_DIR`` environment variable.
    """
    writer = getattr(request.app.state, "writer", None)
    if writer is not None:
        return pathlib.Path(cast(str | pathlib.Path, writer._base_dir))  # noqa: SLF001 — same-service access
    import os

    env_dir = os.environ.get("EVENT_LOG_DIR")
    if env_dir:
        return pathlib.Path(env_dir)
    raise HTTPException(
        status_code=500,
        detail="event log directory not configured",
    )


def _archive_manifest_path(request: Request) -> pathlib.Path | None:
    """Resolve optional replay archive manifest for archive-aware endpoints."""
    _ = request
    resolved = resolve_archive_manifest_path(None)
    if resolved is None:
        return None
    return pathlib.Path(resolved)


def _archive_problem_extensions(request: Request, code: str) -> dict[str, Any]:
    """Build route-local ProblemDetails extensions while preserving trace_id."""
    extensions: dict[str, Any] = {"code": code}
    trace_id = getattr(request.state, "trace_id", None)
    if trace_id is not None:
        extensions["trace_id"] = trace_id
    return extensions


def _archive_problem_response(request: Request, exc: ReplayArchiveError) -> JSONResponse:
    """Map replay archive errors to route-local RFC 7807 responses."""
    if isinstance(exc, ReplayArchiveChecksumError):
        status = 500
        problem_type = "/errors/replay-archive-checksum-mismatch"
        title = "Replay archive checksum mismatch"
        code = "replay_archive_checksum_mismatch"
    elif isinstance(exc, ReplayArchiveMissingSegmentError):
        status = 500
        problem_type = "/errors/replay-archive-missing-segment"
        title = "Replay archive segment missing"
        code = "replay_archive_missing_segment"
    elif isinstance(exc, ReplayArchiveManifestError):
        status = 500
        problem_type = "/errors/replay-archive-manifest-invalid"
        title = "Replay archive manifest invalid"
        code = "replay_archive_manifest_invalid"
    elif isinstance(exc, ReplayArchiveConfigError):
        status = 500
        problem_type = "/errors/replay-archive-config-error"
        title = "Replay archive configuration error"
        code = "replay_archive_config_error"
    elif isinstance(exc, ReplayArchiveConflictError):
        status = 409
        problem_type = "/errors/replay-archive-conflict"
        title = "Replay archive conflict"
        code = "replay_archive_conflict"
    else:
        status = 500
        problem_type = "/errors/replay-archive-manifest-invalid"
        title = "Replay archive manifest invalid"
        code = "replay_archive_manifest_invalid"
    problem = ProblemDetails(
        type=problem_type,
        title=title,
        status=status,
        detail=str(exc),
        instance=str(request.url),
        extensions=_archive_problem_extensions(request, code),
    )
    return JSONResponse(
        content=problem.model_dump(exclude_none=True),
        status_code=status,
        media_type="application/problem+json",
    )


def _snapshot_dir(request: Request) -> pathlib.Path:
    """Resolve the snapshot directory from env or default.

    Reads ``REPLAY_SNAPSHOT_DIR`` from the environment. Falls back to
    ``{event_log_dir}/snapshots`` when unset.
    """
    import os

    env_dir = os.environ.get("REPLAY_SNAPSHOT_DIR")
    if env_dir:
        return pathlib.Path(env_dir)
    return _event_log_dir(request) / "snapshots"


def _summarize_payload(envelope: object) -> dict[str, Any]:
    """Extract a summary dict from an envelope payload for history responses."""
    payload = getattr(envelope, "payload", None)
    if payload is None:
        return {}
    if isinstance(payload, dict):
        # Return up to 8 keys to keep the summary bounded
        return {k: payload[k] for k in list(payload.keys())[:8]}
    if hasattr(payload, "model_dump"):
        dumped = payload.model_dump()
        if isinstance(dumped, dict):
            return {k: dumped[k] for k in list(dumped.keys())[:8]}
    return {}


def _read_task_events_sync(
    event_log_dir: pathlib.Path,
    task_id: str,
) -> list[TaskHistoryEntry]:
    """Read events for a specific task from the event log (sync, for to_thread)."""
    entries: list[TaskHistoryEntry] = []
    for path in sorted(event_log_dir.glob("*.jsonl")):
        try:
            for env in read_log_lines(path):
                payload = env.payload
                if isinstance(payload, dict):
                    env_task_id = payload.get("task_id")
                else:
                    env_task_id = getattr(payload, "task_id", None)
                if env_task_id == task_id:
                    entries.append(
                        TaskHistoryEntry(
                            sequence_number=env.emitted_at_monotonic_ns,
                            emitted_at=env.emitted_at.isoformat(),
                            event_type=env.type,
                            actor_kind=env.actor.kind,
                            actor_id=env.actor.id,
                            trace_id=env.trace_id,
                            payload_summary=_summarize_payload(env),
                        )
                    )
        except FileNotFoundError:
            continue
    entries.sort(key=lambda e: e.sequence_number)
    return entries


# ---------------------------------------------------------------------------
# Story 61-1: GET /v1/events/replay
# ---------------------------------------------------------------------------


@router.get(
    "/events/replay",
    status_code=200,
    response_model=ReplayResponse,
)
async def get_replay(
    request: Request,
    to_timestamp: str | None = Query(None, description="ISO 8601 timestamp target"),
    to_sequence: int | None = Query(None, ge=0, description="Sequence number target"),
) -> ReplayResponse | JSONResponse:
    """GET /v1/events/replay — point-in-time state reconstruction (Story 61-1).

    Accepts exactly one of ``to_timestamp`` or ``to_sequence`` as the replay
    target. Returns the materialized state (tasks + sessions) as of that
    point, computed by replaying the JSONL event log through the same
    Materializer used by the live subscriber.

    Raises 400 if both or neither parameter is supplied.
    """
    # Validate mutual exclusivity
    if to_timestamp is not None and to_sequence is not None:
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one of to_timestamp or to_sequence, not both",
        )
    if to_timestamp is None and to_sequence is None:
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one of to_timestamp or to_sequence",
        )

    # Resolve the target
    if to_sequence is not None:
        up_to: datetime | int = to_sequence
    else:
        try:
            up_to = datetime.fromisoformat(to_timestamp)  # type: ignore[arg-type]
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid ISO 8601 timestamp: {to_timestamp!r}",
            ) from exc

    event_log_dir = _event_log_dir(request)

    # Run replay — the engine is async and creates its own in-memory DB
    try:
        result = await replay_events(
            up_to=up_to,
            event_log_dir=event_log_dir,
            archive_manifest_path=_archive_manifest_path(request),
        )
    except ReplayArchiveError as exc:
        return _archive_problem_response(request, exc)

    # Audit log (NFR-S17)
    actor_id = getattr(request.state, "actor_id", "unknown")
    _log.info(
        "replay_api_request",
        actor_id=actor_id,
        target=to_sequence or to_timestamp,
        event_count=result.metadata.event_count,
        duration_s=result.metadata.replay_duration_s,
    )

    return ReplayResponse(
        state=result.state,
        event_count=result.metadata.event_count,
        sequence_start=result.metadata.sequence_start,
        sequence_end=result.metadata.sequence_end,
        replay_duration_s=result.metadata.replay_duration_s,
    )


# ---------------------------------------------------------------------------
# Story 61-2: GET /v1/tasks/{task_id}/history
# ---------------------------------------------------------------------------


@router.get(
    "/tasks/{task_id}/history",
    status_code=200,
    response_model=TaskHistoryResponse,
)
async def get_task_history(
    request: Request,
    task_id: str = Path(..., pattern=_TASK_ID_PATTERN),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> TaskHistoryResponse:
    """GET /v1/tasks/{task_id}/history — event history for a task (Story 61-2).

    Scans JSONL event-log files for events whose payload references
    ``task_id``, ordered by sequence number. Paginated via ``limit`` /
    ``offset``.

    Returns 404 if no events reference the given task_id.
    """
    event_log_dir = _event_log_dir(request)

    # Read events in a thread to avoid blocking the event loop
    all_entries = await asyncio.to_thread(
        _read_task_events_sync,
        event_log_dir,
        task_id,
    )

    if not all_entries:
        raise HTTPException(
            status_code=404,
            detail=f"No events found for task {task_id}",
        )

    total = len(all_entries)
    page = all_entries[offset : offset + limit]

    # Audit log (NFR-S17)
    actor_id = getattr(request.state, "actor_id", "unknown")
    _log.info(
        "task_history_api_request",
        actor_id=actor_id,
        task_id=task_id,
        event_count=len(page),
        total=total,
    )

    return TaskHistoryResponse(
        events=page,
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Story 62-1: GET /v1/events/replay/validate
# ---------------------------------------------------------------------------


async def _read_live_state(request: Request) -> dict[str, Any]:
    """Read current materialized state from the live database.

    Returns the same ``{"tasks": [...], "sessions": [...]}`` shape used
    by the replay engine so the two can be compared field-by-field.
    """
    session_maker = request.app.state.session_maker
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
        result = await session.execute(select(SessionRow))
        sessions = result.scalars().all()
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


@router.get(
    "/events/replay/validate",
    status_code=200,
    response_model=ValidateReplayResponse,
)
async def validate_replay_endpoint(request: Request) -> ValidateReplayResponse | JSONResponse:
    """GET /v1/events/replay/validate -- compare replayed vs live state (Story 62-1).

    Replays ALL events from the event log to produce the expected state,
    reads the current live materialized state from the database, and
    returns a field-by-field comparison with counts and diffs.
    """
    event_log_dir = _event_log_dir(request)
    live_state = await _read_live_state(request)

    try:
        validation = await validate_replay(
            event_log_dir=event_log_dir,
            live_state=live_state,
            archive_manifest_path=_archive_manifest_path(request),
        )
    except ReplayArchiveError as exc:
        return _archive_problem_response(request, exc)

    # Audit log (NFR-S17)
    actor_id = getattr(request.state, "actor_id", "unknown")
    _log.info(
        "replay_validate_api_request",
        actor_id=actor_id,
        total_fields=validation.total_fields,
        matching_fields=validation.matching_fields,
        mismatching_fields=validation.mismatching_fields,
    )

    return ValidateReplayResponse(
        total_fields=validation.total_fields,
        matching_fields=validation.matching_fields,
        mismatching_fields=validation.mismatching_fields,
        diffs=[
            ValidationFieldDiffResponse(
                table=d.table,
                row_id=d.row_id,
                field=d.field,
                expected=d.expected,
                actual=d.actual,
            )
            for d in validation.diffs
        ],
    )


# ---------------------------------------------------------------------------
# Story 62-2: POST + GET /v1/events/replay/snapshots
# ---------------------------------------------------------------------------


@router.post(
    "/events/replay/snapshots",
    status_code=201,
    response_model=SnapshotEntryResponse,
)
async def create_snapshot_endpoint(request: Request) -> SnapshotEntryResponse:
    """POST /v1/events/replay/snapshots — create a snapshot (Story 62-2).

    Replays the entire event log to produce the current materialized state
    and persists it as a snapshot file. Returns snapshot metadata.
    """
    event_log_dir = _event_log_dir(request)
    snap_dir = _snapshot_dir(request)

    # create_snapshot is sync; run in a thread to avoid blocking the event loop
    info = await asyncio.to_thread(
        _create_snapshot,
        event_log_dir=event_log_dir,
        snapshot_dir=snap_dir,
    )

    # Audit log (NFR-S17)
    actor_id = getattr(request.state, "actor_id", "unknown")
    _log.info(
        "snapshot_created_api",
        actor_id=actor_id,
        snapshot_id=info.snapshot_id,
        sequence_number=info.sequence_number,
        size_bytes=info.size_bytes,
    )

    return SnapshotEntryResponse(
        snapshot_id=info.snapshot_id,
        sequence_number=info.sequence_number,
        timestamp=info.timestamp,
        size_bytes=info.size_bytes,
    )


@router.get(
    "/events/replay/snapshots",
    status_code=200,
    response_model=SnapshotListResponse,
)
async def list_snapshots_endpoint(request: Request) -> SnapshotListResponse:
    """GET /v1/events/replay/snapshots — list all snapshots (Story 62-2).

    Returns all existing snapshots sorted by sequence number.
    """
    snap_dir = _snapshot_dir(request)

    snapshots = await asyncio.to_thread(
        _list_snapshots,
        snapshot_dir=snap_dir,
    )

    # Audit log (NFR-S17)
    actor_id = getattr(request.state, "actor_id", "unknown")
    _log.info(
        "snapshot_list_api",
        actor_id=actor_id,
        total=len(snapshots),
    )

    return SnapshotListResponse(
        snapshots=[
            SnapshotEntryResponse(
                snapshot_id=s.snapshot_id,
                sequence_number=s.sequence_number,
                timestamp=s.timestamp,
                size_bytes=s.size_bytes,
            )
            for s in snapshots
        ],
        total=len(snapshots),
    )


__all__ = [
    "ReplayResponse",
    "SnapshotEntryResponse",
    "SnapshotListResponse",
    "TaskHistoryEntry",
    "TaskHistoryResponse",
    "ValidateReplayResponse",
    "ValidationFieldDiffResponse",
    "router",
]
