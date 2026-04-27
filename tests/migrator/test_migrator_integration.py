"""In-process migrator integration tests (Story 2.14, AC-7 / AC-9).

Four tests exercising the v1.0.0 → v1.0.1 additive migration:

1. ``test_migrator_v1_0_0_to_v1_0_1_in_process_round_trip`` — end-to-end
   shape check: 100-event fixture → migrated `.v1.0.1.jsonl` (100 events,
   each with ``schema_version="1.0.1"`` + ``extensions: {}``) + archived
   original.
2. ``test_migrator_output_round_trips_through_event_envelope`` — every
   migrated line parses cleanly via :func:`events.from_canonical_json`.
3. ``test_migrator_state_equivalence_v1_0_0_vs_v1_0_1`` — the AC-headline
   guarantee: materializing the v1.0.0 archive and the v1.0.1 file into
   two separate in-memory SQLite DBs yields identical observable state
   (``tasks`` + ``sessions`` + ``events.{id, type, task_id, session_id}``).
   ``events.schema_version`` and ``events.payload_json`` are intentionally
   not compared — they ARE expected to drift between versions.
4. ``test_migrator_idempotency_archive_not_overwritten_on_rerun`` —
   defensive: a second migrator run with no current.jsonl exits cleanly
   (SystemExit) without touching the archive.

All tests use ``tmp_path`` for filesystem isolation and an in-process call
to :func:`migrator.main` (no Docker, no subprocess) so the suite
runs in well under 2 s. The Docker-based smoke test for the compose
plumbing is provided by the existing ``just migrator-test-additive`` recipe
(Story 1.3) — Story 2.14 deliberately skips a duplicate Docker test in
this tree (see story Completion Notes).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from events import EventEnvelope, from_canonical_json
from migrator import main as migrator_main
from registry_state.adapters.sqlite_store import get_session
from registry_state.domain.handlers import register_default_handlers
from registry_state.domain.materializer import Materializer
from registry_state.schema import Base, Event, Task
from registry_state.schema import Session as SessionRow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# Shared fixture-path constants
# ---------------------------------------------------------------------------

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_FIXTURE_PATH: Path = (
    _REPO_ROOT / "scripts" / "migrator" / "tests" / "fixtures" / "sample_v1.0.0.jsonl"
)
_EXPECTED_EVENTS = 100
_EXPECTED_TASKS = 25


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stage_fixture(tmp_path: Path) -> Path:
    """Copy the committed 100-event fixture into ``tmp_path/current.jsonl``."""
    target = tmp_path / "current.jsonl"
    shutil.copyfile(_FIXTURE_PATH, target)
    return target


def _run_migrator(event_log_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Invoke the migrator's ``main`` in-process with EVENT_LOG_PATH set."""
    monkeypatch.setenv("EVENT_LOG_PATH", str(event_log_path))
    rc = migrator_main(["python -m migrator", "v1.0.0-to-v1.0.1"])
    assert rc == 0, f"migrator returned non-zero exit code: {rc}"


async def _materialize_log(log_path: Path, engine: AsyncEngine) -> int:
    """Replay every envelope in *log_path* through a fresh Materializer/DB."""
    sm = get_session(engine)
    materializer = Materializer(session_maker=sm)
    register_default_handlers(materializer)
    envelopes: list[EventEnvelope] = []
    for raw in log_path.read_bytes().splitlines():
        line = raw.strip()
        if not line:
            continue
        envelopes.append(from_canonical_json(line))
    return await materializer.apply_many(envelopes)


@pytest_asyncio.fixture
async def fresh_engine() -> AsyncIterator[AsyncEngine]:
    """In-memory async SQLite engine with tables created (StaticPool)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.migrator
def test_migrator_v1_0_0_to_v1_0_1_in_process_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The migrator transforms the 100-event fixture additively + archives."""
    src = _stage_fixture(tmp_path)
    _run_migrator(src, monkeypatch)

    migrated = src.with_suffix(".v1.0.1.jsonl")
    archive = src.with_suffix(".v1.0.0.archive")
    assert migrated.is_file(), f"expected migrated file at {migrated}"
    assert archive.is_file(), f"expected archive at {archive}"
    assert not src.exists(), "original current.jsonl should be moved to archive"

    lines = [ln for ln in migrated.read_text().splitlines() if ln.strip()]
    assert len(lines) == _EXPECTED_EVENTS

    for i, line in enumerate(lines):
        event = json.loads(line)
        assert event["schema_version"] == "1.0.1", f"line {i}: {event['schema_version']!r}"
        assert event.get("extensions") == {}, f"line {i}: extensions={event.get('extensions')!r}"


@pytest.mark.migrator
def test_migrator_output_round_trips_through_event_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every migrated line parses via EventEnvelope.from_canonical_json."""
    src = _stage_fixture(tmp_path)
    _run_migrator(src, monkeypatch)
    migrated = src.with_suffix(".v1.0.1.jsonl")

    parsed_count = 0
    for raw in migrated.read_bytes().splitlines():
        line = raw.strip()
        if not line:
            continue
        env = from_canonical_json(line)
        assert env.schema_version == "1.0.1"
        assert env.extensions == {}
        parsed_count += 1
    assert parsed_count == _EXPECTED_EVENTS


@pytest.mark.migrator
@pytest.mark.asyncio
async def test_migrator_state_equivalence_v1_0_0_vs_v1_0_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Materializing v1.0.0 archive and v1.0.1 output yields identical state.

    AC-7 headline: ``tasks`` + ``sessions`` + ``events.{id, type, task_id,
    session_id}`` are byte-equal across two fresh DBs. ``events.schema_version``
    and ``events.payload_json`` are intentionally NOT compared — those ARE
    expected to differ between v1.0.0 and v1.0.1 (the very point of the
    additive migration).
    """
    src = _stage_fixture(tmp_path)
    _run_migrator(src, monkeypatch)
    archive = src.with_suffix(".v1.0.0.archive")
    migrated = src.with_suffix(".v1.0.1.jsonl")

    # DB-A: replay the v1.0.0 archive.
    engine_a = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine_a.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    count_a = await _materialize_log(archive, engine_a)

    # DB-B: replay the v1.0.1 migrated file.
    engine_b = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine_b.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    count_b = await _materialize_log(migrated, engine_b)

    try:
        assert count_a == count_b == _EXPECTED_EVENTS, (
            f"applied counts diverged: a={count_a} b={count_b}"
        )

        sm_a: async_sessionmaker[AsyncSession] = get_session(engine_a)
        sm_b: async_sessionmaker[AsyncSession] = get_session(engine_b)

        # tasks: id, status, last_event_id, title (repo/hint not on schema).
        async with sm_a() as sa, sm_b() as sb:
            tasks_a = (
                await sa.execute(
                    select(Task.id, Task.status, Task.last_event_id, Task.title).order_by(Task.id)
                )
            ).all()
            tasks_b = (
                await sb.execute(
                    select(Task.id, Task.status, Task.last_event_id, Task.title).order_by(Task.id)
                )
            ).all()
        assert len(tasks_a) == _EXPECTED_TASKS, f"DB-A task count: {len(tasks_a)}"
        assert tasks_a == tasks_b, "tasks rows diverged between v1.0.0 and v1.0.1 replays"

        # sessions: the fixture's 4-event lifecycle does not include
        # ``task.execution.started`` so no session rows materialize.
        # Verify both DBs agree on emptiness (schema-shape equality).
        async with sm_a() as sa, sm_b() as sb:
            sessions_a = (
                await sa.execute(
                    select(SessionRow.id, SessionRow.task_id, SessionRow.worker_kind).order_by(
                        SessionRow.id
                    )
                )
            ).all()
            sessions_b = (
                await sb.execute(
                    select(SessionRow.id, SessionRow.task_id, SessionRow.worker_kind).order_by(
                        SessionRow.id
                    )
                )
            ).all()
        assert sessions_a == sessions_b, "sessions rows diverged between replays"

        # events: id, type, task_id, session_id only — schema_version and
        # payload_json are expected to differ (additive `extensions` field).
        async with sm_a() as sa, sm_b() as sb:
            events_a = (
                await sa.execute(
                    select(Event.id, Event.type, Event.task_id, Event.session_id).order_by(Event.id)
                )
            ).all()
            events_b = (
                await sb.execute(
                    select(Event.id, Event.type, Event.task_id, Event.session_id).order_by(Event.id)
                )
            ).all()
        assert len(events_a) == _EXPECTED_EVENTS
        assert events_a == events_b, "events identity columns diverged between replays"
    finally:
        await engine_a.dispose()
        await engine_b.dispose()


@pytest.mark.migrator
def test_migrator_idempotency_archive_not_overwritten_on_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second migrator run (no current.jsonl) exits cleanly + leaves archive intact."""
    src = _stage_fixture(tmp_path)
    _run_migrator(src, monkeypatch)
    archive = src.with_suffix(".v1.0.0.archive")
    migrated = src.with_suffix(".v1.0.1.jsonl")
    assert archive.is_file()
    assert migrated.is_file()

    archive_bytes_before = archive.read_bytes()
    migrated_bytes_before = migrated.read_bytes()

    # Second run — current.jsonl is gone (moved to archive). The migrator
    # uses ``die()`` which calls ``sys.exit(1)`` → SystemExit propagates.
    monkeypatch.setenv("EVENT_LOG_PATH", str(src))
    with pytest.raises(SystemExit) as exc_info:
        migrator_main(["python -m migrator", "v1.0.0-to-v1.0.1"])
    assert exc_info.value.code == 1

    # Archive + migrated file untouched.
    assert archive.read_bytes() == archive_bytes_before, "archive must not be overwritten"
    assert migrated.read_bytes() == migrated_bytes_before, "migrated file must not be overwritten"
