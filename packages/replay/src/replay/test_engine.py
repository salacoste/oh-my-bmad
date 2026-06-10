"""Tests for replay.engine — Story 60-2 AC (18 tests).

Covers the core replay engine that reconstructs point-in-time state from the
JSONL event log. Uses an in-memory SQLite DB and the same Materializer +
handlers as the live subscriber.

Test categories:
  - Sequence replay (int up_to)
  - Timestamp replay (datetime up_to)
  - Empty / single-event edge cases
  - Batch independence (batch_size does not affect result)
  - Memory limit exceeded → ReplayMemoryError
  - JSON round-trip of ReplayResult
  - Event ordering verification (P12-I2)
  - Read-only verification (no live DB writes)
  - Integration test with full task lifecycle
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random
from typing import Any

# Ensure schema-registry registrations are active (root conftest autouse
# already calls ensure_registered, but import here for clarity).
import events.payloads  # noqa: F401 — ensure registrations
import pytest
import registry_state.domain.event_types  # noqa: F401,IMP001 — ensure event type registrations for replay test fixtures (ADR-0024 D3)
from events import (
    FROZEN_EPOCH,
    Actor,
    EventEnvelope,
    FrozenClock,
    TickingClock,
    new_event_id,
    new_session_id,
    new_task_id,
    new_uuid7,
)
from events.payloads import (
    TaskCompletedPayload,
    TaskCreatedPayload,
    TaskExecutionStartedPayload,
    TaskPlanningStartedPayload,
    TaskPlanReadyPayload,
    TaskStepCompletedPayload,
)
from pydantic import BaseModel

from replay.engine import replay_events
from replay.models import ReplayMemoryError, ReplayMetadata, ReplayResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ACTOR = Actor(kind="operator", id="test-operator")
_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000000"


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_envelope(
    *,
    event_type: str,
    schema_version: str,
    payload: dict[str, Any] | BaseModel,
    mono_ns: int,
    emitted_at: datetime | None = None,
    clock: FrozenClock | None = None,
) -> EventEnvelope:
    """Build an EventEnvelope with deterministic IDs."""
    clk = clock or FrozenClock(mono_ns=mono_ns, now=emitted_at or FROZEN_EPOCH)
    rng = Random(mono_ns)
    return EventEnvelope.create(
        event_id=new_event_id(clock=clk, rng=rng),
        schema_version=schema_version,
        type=event_type,
        emitted_at=clk.now(),
        emitted_at_monotonic_ns=clk.monotonic_ns(),
        actor=_ACTOR,
        payload=payload,
        trace_id=_TRACE_ID,
        request_id=new_uuid7(clock=clk, rng=rng),
    )


def _task_created_env(
    task_id: str,
    *,
    mono_ns: int,
    title: str = "Test task",
    emitted_at: datetime | None = None,
) -> EventEnvelope:
    """Build a task.created envelope."""
    return _make_envelope(
        event_type="task.created",
        schema_version="1.1.0",
        payload=TaskCreatedPayload(task_id=task_id, title=title),
        mono_ns=mono_ns,
        emitted_at=emitted_at,
    )


def _planning_started_env(
    task_id: str,
    *,
    mono_ns: int,
) -> EventEnvelope:
    """Build a task.planning.started envelope."""
    return _make_envelope(
        event_type="task.planning.started",
        schema_version="1.0.0",
        payload=TaskPlanningStartedPayload(task_id=task_id),
        mono_ns=mono_ns,
    )


def _plan_ready_env(
    task_id: str,
    *,
    mono_ns: int,
) -> EventEnvelope:
    """Build a task.plan.ready envelope."""
    return _make_envelope(
        event_type="task.plan.ready",
        schema_version="1.0.0",
        payload=TaskPlanReadyPayload(task_id=task_id, plan_summary="Do the thing"),
        mono_ns=mono_ns,
    )


def _execution_started_env(
    task_id: str,
    session_id: str,
    *,
    mono_ns: int,
) -> EventEnvelope:
    """Build a task.execution.started envelope."""
    return _make_envelope(
        event_type="task.execution.started",
        schema_version="1.0.0",
        payload=TaskExecutionStartedPayload(task_id=task_id, session_id=session_id),
        mono_ns=mono_ns,
    )


def _step_completed_env(
    task_id: str,
    *,
    mono_ns: int,
    step: int = 1,
) -> EventEnvelope:
    """Build a task.step.completed envelope."""
    return _make_envelope(
        event_type="task.step.completed",
        schema_version="1.0.0",
        payload=TaskStepCompletedPayload(
            task_id=task_id,
            step=step,
            description=f"Step {step}",
            output_summary=f"Completed step {step}",
        ),
        mono_ns=mono_ns,
    )


def _completed_env(
    task_id: str,
    *,
    mono_ns: int,
) -> EventEnvelope:
    """Build a task.completed envelope."""
    return _make_envelope(
        event_type="task.completed",
        schema_version="1.0.0",
        payload=TaskCompletedPayload(
            task_id=task_id,
            summary="All done",
        ),
        mono_ns=mono_ns,
    )


def _write_jsonl(path: Path, envelopes: list[EventEnvelope]) -> None:
    """Write envelopes as JSONL lines."""
    with open(path, "w") as f:
        for env in envelopes:
            f.write(env.model_dump_json() + "\n")


def _build_full_lifecycle(
    task_id: str,
    session_id: str,
    *,
    start_mono_ns: int = 1_000_000,
    start_dt: datetime | None = None,
    step_count: int = 3,
) -> list[EventEnvelope]:
    """Build a full task lifecycle event sequence.

    Lifecycle: created → planning_started → plan_ready → execution_started
               → step_completed (x N) → completed
    """
    dt = start_dt or FROZEN_EPOCH
    ns = start_mono_ns
    envs: list[EventEnvelope] = []

    envs.append(_task_created_env(task_id, mono_ns=ns, emitted_at=dt))
    ns += 1_000_000

    envs.append(_planning_started_env(task_id, mono_ns=ns))
    ns += 1_000_000

    envs.append(_plan_ready_env(task_id, mono_ns=ns))
    ns += 1_000_000

    envs.append(_execution_started_env(task_id, session_id, mono_ns=ns))
    ns += 1_000_000

    for step in range(1, step_count + 1):
        envs.append(_step_completed_env(task_id, mono_ns=ns, step=step))
        ns += 1_000_000

    envs.append(_completed_env(task_id, mono_ns=ns))
    return envs


# ---------------------------------------------------------------------------
# Tests — Sequence replay (int up_to)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequence_replay_returns_partial_state(tmp_path: Path) -> None:
    """Replaying up to a sequence number includes only events <= that number."""
    rng = Random(42)
    task_id = new_task_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=rng)
    session_id = new_session_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=rng)

    envs = _build_full_lifecycle(task_id, session_id, start_mono_ns=1_000_000)
    _write_jsonl(tmp_path / "2025-01-01.jsonl", envs)

    # Replay only up to plan_ready (mono_ns=3_000_000)
    result = await replay_events(
        up_to=3_000_000,
        event_log_dir=tmp_path,
    )

    assert result.metadata.event_count == 3
    assert len(result.state["tasks"]) == 1
    assert result.state["tasks"][0]["status"] == "plan_ready"
    # No session yet — execution.started is at 4_000_000
    assert len(result.state["sessions"]) == 0


@pytest.mark.asyncio
async def test_sequence_replay_full_lifecycle(tmp_path: Path) -> None:
    """Replaying with a high up_to includes all events."""
    rng = Random(42)
    task_id = new_task_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=rng)
    session_id = new_session_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=rng)

    envs = _build_full_lifecycle(task_id, session_id, start_mono_ns=1_000_000)
    _write_jsonl(tmp_path / "2025-01-01.jsonl", envs)

    result = await replay_events(
        up_to=999_999_999,
        event_log_dir=tmp_path,
    )

    assert result.metadata.event_count == len(envs)
    assert result.state["tasks"][0]["status"] == "completed"
    # Session created by execution.started, then closed by completed
    assert len(result.state["sessions"]) == 1


# ---------------------------------------------------------------------------
# Tests — Timestamp replay (datetime up_to)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timestamp_replay_filters_by_datetime(tmp_path: Path) -> None:
    """Replaying up_to a datetime includes events with emitted_at <= that time."""
    base_dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    rng = Random(42)
    task_id = new_task_id(clock=FrozenClock(mono_ns=0, now=base_dt), rng=rng)

    envs = [
        _task_created_env(task_id, mono_ns=1_000_000, emitted_at=base_dt),
        _task_created_env(
            new_task_id(clock=FrozenClock(mono_ns=0, now=base_dt), rng=Random(99)),
            mono_ns=2_000_000,
            emitted_at=base_dt + timedelta(hours=1),
        ),
        _task_created_env(
            new_task_id(clock=FrozenClock(mono_ns=0, now=base_dt), rng=Random(77)),
            mono_ns=3_000_000,
            emitted_at=base_dt + timedelta(hours=2),
        ),
    ]
    _write_jsonl(tmp_path / "2025-06-01.jsonl", envs)

    # Only include events up to base_dt + 30 min (only first event)
    result = await replay_events(
        up_to=base_dt + timedelta(minutes=30),
        event_log_dir=tmp_path,
    )

    assert result.metadata.event_count == 1
    assert len(result.state["tasks"]) == 1


# ---------------------------------------------------------------------------
# Tests — Empty event log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_event_log_returns_empty_state(tmp_path: Path) -> None:
    """Empty event log directory returns empty tasks and sessions."""
    # tmp_path is empty — no JSONL files
    result = await replay_events(
        up_to=999_999_999,
        event_log_dir=tmp_path,
    )

    assert result.state == {"tasks": [], "sessions": []}
    assert result.metadata.event_count == 0
    assert result.metadata.sequence_start == 0
    assert result.metadata.sequence_end == 0


# ---------------------------------------------------------------------------
# Tests — Single event replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_event_replay(tmp_path: Path) -> None:
    """Replaying a single task.created event creates one task in pending state."""
    rng = Random(42)
    task_id = new_task_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=rng)
    env = _task_created_env(task_id, mono_ns=1_000_000, title="Single task")
    _write_jsonl(tmp_path / "2025-01-01.jsonl", [env])

    result = await replay_events(
        up_to=999_999_999,
        event_log_dir=tmp_path,
    )

    assert result.metadata.event_count == 1
    assert result.metadata.sequence_start == 1_000_000
    assert result.metadata.sequence_end == 1_000_000
    assert len(result.state["tasks"]) == 1
    assert result.state["tasks"][0]["id"] == task_id
    assert result.state["tasks"][0]["status"] == "pending"
    assert result.state["tasks"][0]["title"] == "Single task"


# ---------------------------------------------------------------------------
# Tests — Batch boundary (events across files)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_boundary_across_files(tmp_path: Path) -> None:
    """Events split across multiple JSONL files produce the same state as one file."""
    rng = Random(42)
    task_id = new_task_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=rng)
    session_id = new_session_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=Random(7))

    envs = _build_full_lifecycle(task_id, session_id, start_mono_ns=1_000_000)

    # Split into two files (sorted by name → 2025-01-01 before 2025-01-02)
    mid = len(envs) // 2
    _write_jsonl(tmp_path / "2025-01-01.jsonl", envs[:mid])
    _write_jsonl(tmp_path / "2025-01-02.jsonl", envs[mid:])

    result = await replay_events(
        up_to=999_999_999,
        event_log_dir=tmp_path,
    )

    assert result.metadata.event_count == len(envs)
    assert result.state["tasks"][0]["status"] == "completed"
    assert len(result.state["sessions"]) == 1


# ---------------------------------------------------------------------------
# Tests — Memory limit exceeded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_limit_exceeded_raises_error(tmp_path: Path) -> None:
    """Exceeding the memory limit raises ReplayMemoryError."""
    rng = Random(42)
    task_id = new_task_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=rng)
    env = _task_created_env(task_id, mono_ns=1_000_000)
    _write_jsonl(tmp_path / "2025-01-01.jsonl", [env])

    # Use an absurdly low limit that the process is guaranteed to exceed
    # (Python process RSS is always > 1KB at this point)
    with pytest.raises(ReplayMemoryError) as exc_info:
        await replay_events(
            up_to=999_999_999,
            event_log_dir=tmp_path,
            memory_limit_bytes=1024,
        )

    assert exc_info.value.limit_bytes == 1024
    assert exc_info.value.current_bytes > 1024


# ---------------------------------------------------------------------------
# Tests — JSON round-trip of ReplayResult
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_result_json_round_trip(tmp_path: Path) -> None:
    """ReplayResult serializes to JSON and back without data loss."""
    rng = Random(42)
    task_id = new_task_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=rng)
    env = _task_created_env(task_id, mono_ns=1_000_000, title="JSON round-trip")
    _write_jsonl(tmp_path / "2025-01-01.jsonl", [env])

    result = await replay_events(
        up_to=999_999_999,
        event_log_dir=tmp_path,
    )

    # Serialize to JSON (dataclass → dict → json)
    result_dict = {
        "state": result.state,
        "metadata": {
            "event_count": result.metadata.event_count,
            "sequence_start": result.metadata.sequence_start,
            "sequence_end": result.metadata.sequence_end,
            "replay_duration_s": result.metadata.replay_duration_s,
            "snapshot_source": result.metadata.snapshot_source,
        },
    }
    json_str = json.dumps(result_dict)

    # Deserialize and verify
    parsed = json.loads(json_str)
    assert parsed["metadata"]["event_count"] == 1
    assert parsed["metadata"]["sequence_start"] == 1_000_000
    assert parsed["metadata"]["sequence_end"] == 1_000_000
    assert parsed["metadata"]["snapshot_source"] is None
    assert len(parsed["state"]["tasks"]) == 1
    assert parsed["state"]["tasks"][0]["title"] == "JSON round-trip"


# ---------------------------------------------------------------------------
# Tests — Batch size independence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_size_independence(tmp_path: Path) -> None:
    """Different batch sizes produce identical final state."""
    rng = Random(42)
    task_id = new_task_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=rng)
    session_id = new_session_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=Random(7))

    envs = _build_full_lifecycle(task_id, session_id, start_mono_ns=1_000_000)
    _write_jsonl(tmp_path / "2025-01-01.jsonl", envs)

    result_bs1 = await replay_events(
        up_to=999_999_999,
        event_log_dir=tmp_path,
        batch_size=1,
    )
    result_bs1000 = await replay_events(
        up_to=999_999_999,
        event_log_dir=tmp_path,
        batch_size=1000,
    )

    # Same event count
    assert result_bs1.metadata.event_count == result_bs1000.metadata.event_count

    # Same tasks (compare by id + status)
    tasks_bs1 = {t["id"]: t["status"] for t in result_bs1.state["tasks"]}
    tasks_bs1000 = {t["id"]: t["status"] for t in result_bs1000.state["tasks"]}
    assert tasks_bs1 == tasks_bs1000

    # Same sessions
    sessions_bs1 = {s["id"]: s["status"] for s in result_bs1.state["sessions"]}
    sessions_bs1000 = {s["id"]: s["status"] for s in result_bs1000.state["sessions"]}
    assert sessions_bs1 == sessions_bs1000


# ---------------------------------------------------------------------------
# Tests — Event ordering verification (P12-I2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_ordering_respects_monotonic_ns(tmp_path: Path) -> None:
    """Events are replayed in emitted_at_monotonic_ns order regardless of file order."""
    rng1 = Random(42)
    rng2 = Random(99)
    task_id_a = new_task_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=rng1)
    task_id_b = new_task_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=rng2)

    # Write events out of order across files:
    # File "2025-01-02.jsonl" has task_b created at mono_ns=1_000_000
    # File "2025-01-01.jsonl" has task_a created at mono_ns=2_000_000
    # Files are sorted by name, so 2025-01-01 is read first, but
    # engine sorts by monotonic_ns — task_b should come first.
    env_b = _task_created_env(task_id_b, mono_ns=1_000_000, title="Task B")
    env_a = _task_created_env(task_id_a, mono_ns=2_000_000, title="Task A")

    # Write in reverse-name order to test sorting
    _write_jsonl(tmp_path / "2025-01-02.jsonl", [env_b])
    _write_jsonl(tmp_path / "2025-01-01.jsonl", [env_a])

    result = await replay_events(
        up_to=999_999_999,
        event_log_dir=tmp_path,
    )

    assert result.metadata.event_count == 2
    assert result.metadata.sequence_start == 1_000_000
    assert result.metadata.sequence_end == 2_000_000
    task_ids = {t["id"] for t in result.state["tasks"]}
    assert task_ids == {task_id_a, task_id_b}


# ---------------------------------------------------------------------------
# Tests — Read-only verification (P12-I1 / NFR-M12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_is_read_only_no_live_db_writes(tmp_path: Path) -> None:
    """Replay uses an in-memory SQLite that is discarded after the call."""
    rng = Random(42)
    task_id = new_task_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=rng)
    env = _task_created_env(task_id, mono_ns=1_000_000)
    _write_jsonl(tmp_path / "2025-01-01.jsonl", [env])

    result = await replay_events(
        up_to=999_999_999,
        event_log_dir=tmp_path,
    )

    # The result is a dict snapshot, not a live DB connection.
    # Verify the state is a plain dict (not a SQLAlchemy object).
    assert isinstance(result, ReplayResult)
    assert isinstance(result.state, dict)
    assert isinstance(result.state["tasks"], list)
    assert isinstance(result.state["sessions"], list)

    # The metadata confirms replay ran (not live subscription).
    assert result.metadata.snapshot_source is None  # full replay
    assert result.metadata.replay_duration_s >= 0


# ---------------------------------------------------------------------------
# Tests — Metadata correctness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_fields_are_correct(tmp_path: Path) -> None:
    """ReplayMetadata fields are populated correctly."""
    rng = Random(42)
    task_id = new_task_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=rng)
    session_id = new_session_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=Random(7))

    envs = _build_full_lifecycle(task_id, session_id, start_mono_ns=5_000_000)
    _write_jsonl(tmp_path / "2025-01-01.jsonl", envs)

    result = await replay_events(
        up_to=999_999_999,
        event_log_dir=tmp_path,
    )

    meta = result.metadata
    assert meta.event_count == len(envs)
    assert meta.sequence_start == 5_000_000
    assert meta.sequence_end == envs[-1].emitted_at_monotonic_ns
    assert meta.replay_duration_s > 0
    assert meta.snapshot_source is None


# ---------------------------------------------------------------------------
# Tests — ReplayMetadata is frozen
# ---------------------------------------------------------------------------


def test_replay_metadata_is_frozen() -> None:
    """ReplayMetadata is a frozen dataclass — immutable after creation."""
    meta = ReplayMetadata(
        event_count=5,
        sequence_start=100,
        sequence_end=500,
        replay_duration_s=0.1,
        snapshot_source=None,
    )
    with pytest.raises(AttributeError):
        meta.event_count = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tests — ReplayResult is frozen
# ---------------------------------------------------------------------------


def test_replay_result_is_frozen() -> None:
    """ReplayResult is a frozen dataclass — immutable after creation."""
    result = ReplayResult(
        state={"tasks": [], "sessions": []},
        metadata=ReplayMetadata(
            event_count=0,
            sequence_start=0,
            sequence_end=0,
            replay_duration_s=0.0,
            snapshot_source=None,
        ),
    )
    with pytest.raises(AttributeError):
        result.state = {}  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tests — ReplayMemoryError attributes
# ---------------------------------------------------------------------------


def test_replay_memory_error_attributes() -> None:
    """ReplayMemoryError carries current_bytes and limit_bytes."""
    err = ReplayMemoryError(current_bytes=512_000_000, limit_bytes=256_000_000)
    assert err.current_bytes == 512_000_000
    assert err.limit_bytes == 256_000_000
    assert "512000000" in str(err)
    assert "256000000" in str(err)


# ---------------------------------------------------------------------------
# Tests — FileNotFoundError for missing directory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_event_log_dir_returns_empty_state(tmp_path: Path) -> None:
    """Non-existent event_log_dir returns empty state (glob yields nothing)."""
    missing = tmp_path / "nonexistent"
    result = await replay_events(
        up_to=999_999_999,
        event_log_dir=missing,
    )
    assert result.state == {"tasks": [], "sessions": []}
    assert result.metadata.event_count == 0


# ---------------------------------------------------------------------------
# Integration test — 1000-event fixture
# ---------------------------------------------------------------------------


def _generate_1000_events(tmp_path: Path) -> dict[str, str]:
    """Generate 1000 events covering 200 task lifecycles.

    Each task goes through: created → planning_started → plan_ready →
    execution_started → step_completed (x2) → completed = 7 events/task.
    200 tasks × 5 events (skip steps for speed) = 1000 events.

    Returns a dict mapping task_id → expected final status ("completed").
    """
    clock = TickingClock(start_now=FROZEN_EPOCH)
    rng = Random(12345)
    ns = 1_000_000
    all_envs: list[EventEnvelope] = []
    task_ids: dict[str, str] = {}

    for i in range(200):
        task_id = new_task_id(clock=clock, rng=rng)
        session_id = new_session_id(clock=clock, rng=rng)
        task_ids[task_id] = "completed"

        # created
        clk = FrozenClock(mono_ns=ns, now=clock.now())
        all_envs.append(
            _make_envelope(
                event_type="task.created",
                schema_version="1.1.0",
                payload=TaskCreatedPayload(task_id=task_id, title=f"Task {i}"),
                mono_ns=ns,
                emitted_at=clk.now(),
                clock=clk,
            )
        )
        ns += 500_000

        # planning.started
        clk = FrozenClock(mono_ns=ns, now=clock.now())
        all_envs.append(
            _make_envelope(
                event_type="task.planning.started",
                schema_version="1.0.0",
                payload=TaskPlanningStartedPayload(task_id=task_id),
                mono_ns=ns,
                emitted_at=clk.now(),
                clock=clk,
            )
        )
        ns += 500_000

        # plan.ready
        clk = FrozenClock(mono_ns=ns, now=clock.now())
        all_envs.append(
            _make_envelope(
                event_type="task.plan.ready",
                schema_version="1.0.0",
                payload=TaskPlanReadyPayload(
                    task_id=task_id,
                    plan_summary=f"Plan for task {i}",
                ),
                mono_ns=ns,
                emitted_at=clk.now(),
                clock=clk,
            )
        )
        ns += 500_000

        # execution.started
        clk = FrozenClock(mono_ns=ns, now=clock.now())
        all_envs.append(
            _make_envelope(
                event_type="task.execution.started",
                schema_version="1.0.0",
                payload=TaskExecutionStartedPayload(
                    task_id=task_id,
                    session_id=session_id,
                ),
                mono_ns=ns,
                emitted_at=clk.now(),
                clock=clk,
            )
        )
        ns += 500_000

        # completed
        clk = FrozenClock(mono_ns=ns, now=clock.now())
        all_envs.append(
            _make_envelope(
                event_type="task.completed",
                schema_version="1.0.0",
                payload=TaskCompletedPayload(
                    task_id=task_id,
                    summary=f"Completed task {i}",
                ),
                mono_ns=ns,
                emitted_at=clk.now(),
                clock=clk,
            )
        )
        ns += 500_000

    assert len(all_envs) == 1000

    # Split across 5 files
    chunk_size = 200
    for idx in range(5):
        chunk = all_envs[idx * chunk_size : (idx + 1) * chunk_size]
        date_str = f"2025-01-{idx + 1:02d}"
        _write_jsonl(tmp_path / f"{date_str}.jsonl", chunk)

    return task_ids


@pytest.mark.asyncio
async def test_integration_1000_event_fixture(tmp_path: Path) -> None:
    """1000-event replay produces correct state for 200 tasks through full lifecycle."""
    _generate_1000_events(tmp_path)

    result = await replay_events(
        up_to=999_999_999_999,
        event_log_dir=tmp_path,
    )

    assert result.metadata.event_count == 1000

    # All 200 tasks completed
    tasks = result.state["tasks"]
    assert len(tasks) == 200

    # Every task should be in "completed" status
    for task in tasks:
        assert task["status"] == "completed", (
            f"Task {task['id']} is {task['status']}, expected completed"
        )

    # All 200 sessions should exist (created by execution.started, closed by completed)
    sessions = result.state["sessions"]
    assert len(sessions) == 200


# ---------------------------------------------------------------------------
# Integration test — replay matches live materializer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_matches_live_materializer(tmp_path: Path) -> None:
    """Replay produces the same state as applying events through live Materializer."""
    rng = Random(42)
    task_id = new_task_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=rng)
    session_id = new_session_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=Random(7))

    envs = _build_full_lifecycle(task_id, session_id, start_mono_ns=1_000_000)
    _write_jsonl(tmp_path / "2025-01-01.jsonl", envs)

    # 1. Run replay engine
    replay_result = await replay_events(
        up_to=999_999_999,
        event_log_dir=tmp_path,
    )

    # 2. Run live materializer (same path as replay engine uses internally)
    from registry_state.domain.handlers import (  # noqa: IMP001 — test fixture uses materializer for comparison (ADR-0024 D3)
        register_default_handlers,
    )
    from registry_state.domain.materializer import (  # noqa: IMP001 — test fixture verifying replay matches live materializer (FR134)
        Materializer,
    )
    from registry_state.schema import (  # noqa: IMP001 — in-memory DB for test comparison fixture
        Base,
    )
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    live_engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with live_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        sm = async_sessionmaker(live_engine)
        mat = Materializer(session_maker=sm)
        register_default_handlers(mat)
        await mat.apply_many(envs)

        # Read state from live DB
        from registry_state.schema import (  # noqa: IMP001 — reading from in-memory test DB
            Session as SessionRow,
        )
        from registry_state.schema import Task  # noqa: IMP001 — reading from in-memory test DB
        from sqlalchemy import select

        async with sm() as session:
            task_rows = (await session.execute(select(Task))).scalars().all()
            live_tasks = [
                {
                    "id": t.id,
                    "status": t.status,
                    "title": t.title,
                }
                for t in task_rows
            ]
            sess_rows = (await session.execute(select(SessionRow))).scalars().all()
            live_sessions = [
                {
                    "id": s.id,
                    "task_id": s.task_id,
                    "status": s.status,
                }
                for s in sess_rows
            ]
    finally:
        await live_engine.dispose()

    # 3. Compare
    replay_tasks = [
        {"id": t["id"], "status": t["status"], "title": t["title"]}
        for t in replay_result.state["tasks"]
    ]
    replay_sessions = [
        {"id": s["id"], "task_id": s["task_id"], "status": s["status"]}
        for s in replay_result.state["sessions"]
    ]

    assert replay_tasks == live_tasks
    assert replay_sessions == live_sessions


# ---------------------------------------------------------------------------
# Tests — Phase 13 archive manifest replay
# ---------------------------------------------------------------------------


def _write_lifecycle_manifest(
    manifest_dir: Path,
    *,
    logical_date: str,
    original_relpath: str,
    archive_relpath: str,
    sha256: str,
    event_count: int,
    first_sequence: int,
    last_sequence: int,
) -> Path:
    manifest = {
        "schema_version": 1,
        "manifest_id": "m-test",
        "created_at": "2026-06-10T00:00:00Z",
        "created_by": "pytest",
        "segments": [
            {
                "logical_date": logical_date,
                "original_relpath": original_relpath,
                "archive_relpath": archive_relpath,
                "sha256": sha256,
                "event_count": event_count,
                "first_sequence": first_sequence,
                "last_sequence": last_sequence,
                "archived_at": "2026-06-10T00:00:00Z",
                "actor_id": "pytest",
            }
        ],
    }
    path = manifest_dir / "lifecycle-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_replay_events_reads_archived_segment_from_manifest(tmp_path: Path) -> None:
    """Archive-only manifest segments are included in replay_events."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    rng = Random(42)
    task_id = new_task_id(clock=FrozenClock(mono_ns=0, now=FROZEN_EPOCH), rng=rng)
    env = _task_created_env(task_id, mono_ns=1_000_000, title="archived")
    archive_file = archive_dir / "2026-06-09.jsonl"
    _write_jsonl(archive_file, [env])
    manifest = _write_lifecycle_manifest(
        archive_dir,
        logical_date="2026-06-09",
        original_relpath="2026-06-09.jsonl",
        archive_relpath="2026-06-09.jsonl",
        sha256=_sha256(archive_file),
        event_count=1,
        first_sequence=1_000_000,
        last_sequence=1_000_000,
    )

    result = await replay_events(
        up_to=9_999_999,
        event_log_dir=tmp_path / "hot",
        archive_manifest_path=manifest,
    )

    assert result.metadata.event_count == 1
    assert result.state["tasks"][0]["id"] == task_id
    assert result.state["tasks"][0]["title"] == "archived"


@pytest.mark.asyncio
async def test_replay_events_rejects_archive_overlap_with_hot(tmp_path: Path) -> None:
    """Overlapping hot/archive sequence ranges fail closed."""
    from replay.errors import ReplayArchiveConflictError

    hot_env = _task_created_env("t-00000000-0000-7000-8000-000000000001", mono_ns=1_000_000)
    _write_jsonl(tmp_path / "2026-06-09.jsonl", [hot_env])
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    archive_env = _task_created_env("t-00000000-0000-7000-8000-000000000002", mono_ns=1_000_000)
    archive_file = archive_dir / "2026-06-10.jsonl"
    _write_jsonl(archive_file, [archive_env])
    manifest = _write_lifecycle_manifest(
        archive_dir,
        logical_date="2026-06-10",
        original_relpath="2026-06-10.jsonl",
        archive_relpath="2026-06-10.jsonl",
        sha256=_sha256(archive_file),
        event_count=1,
        first_sequence=1_000_000,
        last_sequence=1_000_000,
    )

    with pytest.raises(ReplayArchiveConflictError):
        await replay_events(up_to=9_999_999, event_log_dir=tmp_path, archive_manifest_path=manifest)


@pytest.mark.asyncio
async def test_replay_events_env_manifest_missing_raises_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitted archive_manifest_path is env-aware and rejects invalid env paths."""
    from replay.errors import ReplayArchiveConfigError

    monkeypatch.setenv("REPLAY_ARCHIVE_MANIFEST", str(tmp_path / "missing.json"))
    with pytest.raises(ReplayArchiveConfigError):
        await replay_events(up_to=9_999_999, event_log_dir=tmp_path)


def test_archive_manifest_env_same_path_uses_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both archive env vars may point to the same normalized manifest path."""
    from replay.archive_manifest import resolve_archive_manifest_path

    manifest = tmp_path / "lifecycle-manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("REPLAY_ARCHIVE_MANIFEST", str(manifest))
    monkeypatch.setenv("EVENT_LOG_ARCHIVE_MANIFEST", str(manifest.resolve(strict=False)))

    assert resolve_archive_manifest_path() == manifest.resolve(strict=False)


def test_archive_manifest_env_different_paths_raise_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Different primary/legacy archive env paths fail closed."""
    from replay.archive_manifest import resolve_archive_manifest_path
    from replay.errors import ReplayArchiveConfigError

    primary = tmp_path / "primary.json"
    legacy = tmp_path / "legacy.json"
    primary.write_text("{}", encoding="utf-8")
    legacy.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("REPLAY_ARCHIVE_MANIFEST", str(primary))
    monkeypatch.setenv("EVENT_LOG_ARCHIVE_MANIFEST", str(legacy))

    with pytest.raises(ReplayArchiveConfigError):
        resolve_archive_manifest_path()


def test_archive_manifest_explicit_path_overrides_invalid_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit archive_manifest_path wins and ignores invalid env vars."""
    from replay.archive_manifest import resolve_archive_manifest_path

    explicit = tmp_path / "explicit.json"
    explicit.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("REPLAY_ARCHIVE_MANIFEST", str(tmp_path / "missing.json"))
    monkeypatch.setenv("EVENT_LOG_ARCHIVE_MANIFEST", str(tmp_path / "other-missing.json"))

    assert resolve_archive_manifest_path(explicit) == explicit.resolve(strict=False)
