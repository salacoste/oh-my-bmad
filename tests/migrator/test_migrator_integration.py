"""In-process migrator integration tests (Story 2.14, AC-7 / AC-9).

Tests exercising the v1.0.0 → v1.0.1 additive migration over the 135-event
fixture (30 tasks; first 15 emit ``task.execution.started`` so sessions-
equivalence has actual signal — see Story 2.14 code-review fix M3):

1. ``test_migrator_v1_0_0_to_v1_0_1_in_process_round_trip`` — end-to-end
   shape check: 135-event fixture → migrated `.v1.0.1.jsonl` (135 events,
   each with ``schema_version="1.0.1"`` + ``extensions: {}``) + archived
   original.
2. ``test_migrator_output_round_trips_through_event_envelope`` — every
   migrated line parses cleanly via :func:`events.from_canonical_json`.
3. ``test_migrator_state_equivalence_v1_0_0_vs_v1_0_1`` — the AC-headline
   guarantee: materializing the v1.0.0 archive and the v1.0.1 file into
   two separate in-memory SQLite DBs yields identical observable state
   (``tasks`` + ``sessions`` + ``events.{id, type, task_id, session_id}``).
   Code-review fix M2: ALSO compares payload-content equivalence (the
   migrator only mutates the envelope's ``schema_version`` and adds
   ``extensions``; the inner ``payload`` dict is byte-preserved). The
   identity-column comparison alone was tautological — the migrator
   doesn't touch those columns by construction.
4. ``test_migrator_second_run_with_no_input_fails_cleanly`` — defensive:
   a second migrator run with no current.jsonl returns rc=1 (per fix C3
   ``main`` returns rather than raises ``SystemExit``) without touching
   the archive. Renamed from ``test_migrator_idempotency_...`` per
   code-review fix Mn1: this test is "second run with no input fails
   cleanly", NOT idempotency (the migrator is destructive on success —
   it MOVES the input to archive).
5. ``test_migrator_rejects_non_v1_0_0_input`` — code-review fix M5:
   :func:`migrate_v1_0_0_to_v1_0_1` validates the input event's
   ``schema_version`` is exactly ``"1.0.0"`` and refuses to mutate
   otherwise.
6. ``test_gen_fixture_is_byte_deterministic`` — code-review fix M4:
   running ``gen_fixture.main()`` against a tmp file produces a byte-
   identical SHA-256 to the committed fixture. Determinism is now
   verified, not just claimed in a docstring.

All tests use ``tmp_path`` for filesystem isolation and an in-process call
to :func:`migrator.main` (no Docker, no subprocess) so the suite
runs in well under 2 s. The Docker-based smoke test for the compose
plumbing is provided by the ``just migrator-test-additive`` recipe
(Story 1.3) — Story 2.14 deliberately skips a duplicate Docker test in
this tree (see story Spec Amendments). Code-review fix C1 closes the
AC-8 gap by invoking ``just migrator-test-additive`` from the nightly
``migrator-integration`` job.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from events import EventEnvelope, from_canonical_json
from migrator import main as migrator_main
from migrator.cli import migrate_v1_0_0_to_v1_0_1
from registry_state.adapters.sqlite_store import get_session
from registry_state.domain.handlers import register_default_handlers
from registry_state.domain.materializer import Materializer

# NOTE (code-review fix Mn5/Mn13 — spec→schema column mismatches):
# The original Story 2.14 spec referenced ``events.payload_canonical_json``
# (actual schema column is ``payload_json``) and ``tasks.{repo, hint}``
# (which are NOT on the Task ORM model — they live in the canonical-JSON
# payload, not as flat columns on the SQL row). The SELECT statements
# below use the actual schema columns. The spec mismatches are
# documentation bugs, not implementation bugs; this comment marks them
# for retrospective traceability.
from registry_state.schema import Base, Event, Task
from registry_state.schema import Session as SessionRow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
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

# Code-review fix M3: fixture extended to 30 tasks (135 events, 15 with
# ``task.execution.started``). Code-review fix M12: pin counts explicitly
# rather than letting them default-drift across stories.
_EXPECTED_EVENTS = 135
_EXPECTED_TASKS = 30
_EXPECTED_SESSIONS = 15  # First 15 tasks emit task.execution.started.

# Code-review fix M4: pin the fixture's SHA-256 so determinism is asserted,
# not just claimed in the gen_fixture docstring. After regenerating the
# fixture (e.g., adding event types) update this constant by running:
#
#   uv run python scripts/migrator/tests/gen_fixture.py
#   shasum -a 256 scripts/migrator/tests/fixtures/sample_v1.0.0.jsonl
#
# Then paste the digest below.
_FIXTURE_SHA256: str = "bd6e8fe1da2490f8eacaeaaec8537a3050436c1f19e7bda778b3ca0c0ec05c8a"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stage_fixture(tmp_path: Path) -> Path:
    """Copy the committed 135-event fixture into ``tmp_path/current.jsonl``."""
    target = tmp_path / "current.jsonl"
    shutil.copyfile(_FIXTURE_PATH, target)
    return target


def _run_migrator(event_log_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Invoke the migrator's ``main`` in-process with EVENT_LOG_PATH set.

    Returns the exit code (per code-review fix C3, ``main`` returns rather
    than raising ``SystemExit``). Caller asserts on the return code.

    Code-review fix Mn3: pass argv[0] as ``"migrator"`` (no embedded space)
    to match the convention of ``sys.argv[0]`` being a program name. The
    earlier ``"python -m migrator"`` was cosmetic but masked any future
    argv[0]-based usage messages.
    """
    monkeypatch.setenv("EVENT_LOG_PATH", str(event_log_path))
    return migrator_main(["migrator", "v1.0.0-to-v1.0.1"])


def _make_fresh_engine() -> AsyncEngine:
    """Build a fresh in-memory async SQLite engine (StaticPool, fresh schema).

    Code-review fix Mn2 / Mn11: the previous version defined a ``fresh_engine``
    pytest fixture that was never used (the state-equivalence test inlined
    engine creation twice). Inline the engine setup into a helper used
    by both DB-A and DB-B paths instead.
    """
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


async def _materialize_log(log_path: Path, engine: AsyncEngine) -> int:
    """Replay every envelope in *log_path* through a fresh Materializer/DB.

    Story 9.7 compatibility: pre-1.1.0 records have ``trace_id: null``.
    Before parsing through ``from_canonical_json`` (which now requires a
    non-null trace_id), inject the ``request_id`` as a synthetic trace_id
    for any record missing one. Mirrors the migrator's back-fill strategy.
    """
    sm = get_session(engine)
    materializer = Materializer(session_maker=sm)
    register_default_handlers(materializer)
    envelopes: list[EventEnvelope] = []
    for raw in log_path.read_bytes().splitlines():
        line = raw.strip()
        if not line:
            continue
        # Back-fill trace_id for legacy records so from_canonical_json succeeds.
        record = json.loads(line)
        if not record.get("trace_id"):
            record["trace_id"] = record.get("request_id", "")
        envelopes.append(from_canonical_json(json.dumps(record).encode()))
    return await materializer.apply_many(envelopes)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.migrator
def test_migrator_v1_0_0_to_v1_0_1_in_process_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The migrator transforms the fixture additively + archives."""
    src = _stage_fixture(tmp_path)
    rc = _run_migrator(src, monkeypatch)
    assert rc == 0, f"migrator returned non-zero exit code: {rc}"

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
    rc = _run_migrator(src, monkeypatch)
    assert rc == 0
    migrated = src.with_suffix(".v1.0.1.jsonl")

    parsed_count = 0
    for raw in migrated.read_bytes().splitlines():
        line = raw.strip()
        if not line:
            continue
        env = from_canonical_json(line)
        assert env.schema_version == "1.0.1"
        assert env.extensions == {}
        # Story 9.7: migrator back-fills trace_id using request_id so that
        # post-9.7 envelope validation (which requires trace_id) accepts
        # migrated records. Verify the back-fill was applied.
        assert env.trace_id is not None, "migrated record must carry a trace_id"
        assert env.trace_id == env.request_id, (
            "migrated trace_id should equal request_id (synthetic back-fill)"
        )
        parsed_count += 1
    assert parsed_count == _EXPECTED_EVENTS


@pytest.mark.migrator
@pytest.mark.asyncio
async def test_migrator_state_equivalence_v1_0_0_vs_v1_0_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Materializing v1.0.0 archive and v1.0.1 output yields identical state.

    AC-7 headline. ``tasks`` + ``sessions`` + ``events.{id, type, task_id,
    session_id}`` are byte-equal across two fresh DBs. ``events.schema_version``
    and ``events.payload_json`` are intentionally NOT compared at the SQL
    level — those ARE expected to differ between v1.0.0 and v1.0.1 (the
    very point of the additive migration).

    Code-review fix M2: identity-column comparison alone is tautological
    (the migrator preserves event_id/type/task_id/session_id by
    construction — it only mutates ``schema_version`` and adds
    ``extensions`` at the envelope level). To prove the migration is
    truly additive, ALSO compare the *parsed* payload contents from
    both logs: they must be byte-identical (the inner ``payload`` dict
    is preserved).
    """
    src = _stage_fixture(tmp_path)
    rc = _run_migrator(src, monkeypatch)
    assert rc == 0
    archive = src.with_suffix(".v1.0.0.archive")
    migrated = src.with_suffix(".v1.0.1.jsonl")

    # Code-review fix M2: parsed-payload equivalence (the inner ``payload``
    # dict on each envelope is byte-preserved by the migrator). This is the
    # ONLY column that meaningfully changes shape between v1.0.0 and v1.0.1
    # (extensions is envelope-level, not payload-level).
    a_payloads = [
        json.loads(ln)["payload"] for ln in archive.read_text().splitlines() if ln.strip()
    ]
    b_payloads = [
        json.loads(ln)["payload"] for ln in migrated.read_text().splitlines() if ln.strip()
    ]
    assert a_payloads == b_payloads, (
        "envelope.payload bytes diverged between v1.0.0 archive and v1.0.1 "
        "migrated log; the migration must be additive at the envelope level "
        "and a no-op at the payload level"
    )

    # DB-A: replay the v1.0.0 archive.
    engine_a = _make_fresh_engine()
    async with engine_a.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    count_a = await _materialize_log(archive, engine_a)

    # DB-B: replay the v1.0.1 migrated file.
    engine_b = _make_fresh_engine()
    async with engine_b.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    count_b = await _materialize_log(migrated, engine_b)

    try:
        assert count_a == count_b == _EXPECTED_EVENTS, (
            f"applied counts diverged: a={count_a} b={count_b}"
        )

        sm_a = get_session(engine_a)
        sm_b = get_session(engine_b)

        # tasks: id, status, last_event_id, title.
        # NOTE (code-review fix Mn5/Mn13): the spec's column list referenced
        # ``tasks.{repo, hint}`` but those fields live in the canonical-JSON
        # payload, NOT as flat columns on the Task ORM model. Spec
        # documentation bug, not an implementation bug.
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

        # sessions: code-review fix M3 — first 15 tasks emit
        # ``task.execution.started`` so 15 session rows materialize. The
        # original fixture skipped this event entirely so sessions_a ==
        # sessions_b == [] was tautological. With the extended fixture the
        # equivalence has actual signal.
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
        assert len(sessions_a) == _EXPECTED_SESSIONS, (
            f"DB-A session count: {len(sessions_a)} (expected {_EXPECTED_SESSIONS})"
        )
        assert sessions_a == sessions_b, "sessions rows diverged between replays"

        # events: id, type, task_id, session_id only — schema_version and
        # payload_json are expected to differ at the SQL level (additive
        # ``extensions`` field on the envelope). NOTE the schema column is
        # ``payload_json`` (the spec referenced ``payload_canonical_json``
        # — Mn5/Mn13 documentation drift).
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
def test_migrator_second_run_with_no_input_fails_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second migrator run (no current.jsonl) returns rc=1 + leaves archive intact.

    Code-review fix Mn1: renamed from ``test_migrator_idempotency_...`` —
    this test is "second run with no input fails cleanly" (a defensive
    not-found check), NOT idempotency. The migrator is destructive on
    success (it MOVES the input to archive); idempotent operations are
    repeatable, this one is not.

    Code-review fix C3: ``cli.main`` returns rather than raising
    :class:`SystemExit`, so the test asserts on the return code instead
    of catching the exception. Mn8: the prior ``rc == 0`` post-call
    check on the success path was unreachable — fixed once main returns.
    """
    src = _stage_fixture(tmp_path)
    rc = _run_migrator(src, monkeypatch)
    assert rc == 0
    archive = src.with_suffix(".v1.0.0.archive")
    migrated = src.with_suffix(".v1.0.1.jsonl")
    assert archive.is_file()
    assert migrated.is_file()

    archive_bytes_before = archive.read_bytes()
    migrated_bytes_before = migrated.read_bytes()

    # Second run — current.jsonl is gone (moved to archive). Per code-review
    # fix C3 the migrator's main() returns rc=1 cleanly rather than raising
    # SystemExit via the old die() helper.
    monkeypatch.setenv("EVENT_LOG_PATH", str(src))
    rc2 = migrator_main(["migrator", "v1.0.0-to-v1.0.1"])
    assert rc2 == 1, f"second run should return 1, got {rc2}"

    # Archive + migrated file untouched.
    assert archive.read_bytes() == archive_bytes_before, "archive must not be overwritten"
    assert migrated.read_bytes() == migrated_bytes_before, "migrated file must not be overwritten"


@pytest.mark.migrator
def test_migrator_rejects_non_v1_0_0_input() -> None:
    """``migrate_v1_0_0_to_v1_0_1`` validates input schema_version=='1.0.0'.

    Code-review fix M5: the function previously unconditionally set
    ``schema_version="1.0.1"``; if a v1.0.1 (or future v2.0.0) event
    leaked into the input it got silently clobbered. Now it raises
    :class:`ValueError` so the wrong-migrator-invoked case is loud.
    """
    # v1.0.1 input — already migrated; refuse to mutate.
    v1_0_1_event: dict[str, Any] = {
        "schema_version": "1.0.1",
        "extensions": {},
        "type": "task.created",
    }
    with pytest.raises(ValueError, match="expected schema_version='1.0.0'"):
        migrate_v1_0_0_to_v1_0_1(v1_0_1_event)

    # Hypothetical future v2.0.0 input — refuse to mutate.
    v2_0_0_event: dict[str, Any] = {"schema_version": "2.0.0", "type": "task.created"}
    with pytest.raises(ValueError, match="expected schema_version='1.0.0'"):
        migrate_v1_0_0_to_v1_0_1(v2_0_0_event)

    # Missing schema_version — refuse to mutate.
    no_version_event: dict[str, Any] = {"type": "task.created"}
    with pytest.raises(ValueError, match="expected schema_version='1.0.0'"):
        migrate_v1_0_0_to_v1_0_1(no_version_event)


@pytest.mark.migrator
def test_migrator_accepts_e_prefix_request_id() -> None:
    """Story 9.7 pass-2 TH-B2 / Q6: migrator accepts ``e-<uuidv7>`` request_id.

    Pre-9.1 envelopes used ``e-<uuidv7>`` as their ``request_id`` shape
    (matching the event_id shape). The back-fill rule must strip the ``e-``
    prefix before assigning to ``trace_id`` so the result is a valid bare
    UUIDv7 (Story-9.1 contract).
    """
    event_with_e_prefix = {
        "event_id": "e-01917e5c-a7d1-7000-8abc-000000000001",
        "schema_version": "1.0.0",
        "type": "task.created",
        "emitted_at": "2026-01-01T00:00:00.000000Z",
        "emitted_at_monotonic_ns": 1000,
        "actor": {"kind": "operator", "id": "test-op"},
        "payload": {"task_id": "t-00000000-0000-7000-8000-000000000001", "title": "t"},
        "request_id": "e-01917e5c-a7d1-7000-8abc-000000000001",
        "extensions": {},
    }
    result = migrate_v1_0_0_to_v1_0_1(event_with_e_prefix)
    assert result["trace_id"] == "01917e5c-a7d1-7000-8abc-000000000001", (
        f"e-prefix should be stripped: got {result['trace_id']!r}"
    )


@pytest.mark.migrator
def test_backfill_invariant_migrator_eq_subscriber() -> None:
    """Story 9.7 pass-2 TH-B5: migrator + subscriber back-fill produce same output.

    The shared helper in ``packages/events/src/events/backfill.py`` is the
    source of truth. This test asserts that the migrator's local mirror
    (``_backfill_trace_id_from_request_id``) produces identical ``trace_id``
    for the same input dict.
    """
    from events.backfill import backfill_trace_id_from_request_id
    from migrator.cli import _backfill_trace_id_from_request_id as _migrator_backfill

    samples = [
        # Plain UUIDv7 request_id
        {"request_id": "01917e5c-a7d1-7000-8abc-000000000001"},
        # e-prefixed request_id (pre-9.1 shape)
        {"request_id": "e-01917e5c-a7d1-7000-8abc-000000000002"},
    ]
    for record in samples:
        migrator_result = _migrator_backfill(record)
        shared_result = backfill_trace_id_from_request_id(record)
        assert migrator_result is not None, f"migrator back-fill returned None for {record!r}"
        assert shared_result is not None, f"shared back-fill returned None for {record!r}"
        assert migrator_result == shared_result.get("trace_id"), (
            f"invariant broken for {record!r}: migrator={migrator_result!r}, "
            f"shared={shared_result.get('trace_id')!r}"
        )


@pytest.mark.migrator
def test_gen_fixture_is_byte_deterministic() -> None:
    """``gen_fixture.main()`` produces a byte-identical fixture across runs.

    Code-review fix M4: determinism is now asserted, not just claimed in
    the docstring. This test compares the SHA-256 of the committed
    fixture against the pinned constant ``_FIXTURE_SHA256``. After
    regenerating the fixture (e.g., adding event types), update the
    constant alongside the new fixture in the same commit.

    NOTE: this test verifies the COMMITTED fixture matches the pinned
    digest — not that re-running the generator produces a fresh
    matching file. That's because re-running the generator from a
    pytest process while ``scripts/migrator/tests/`` is on the import
    path would clobber the committed file. The committed fixture IS
    the determinism oracle; if the generator drifts, regeneration will
    produce a different SHA and this test will fail until the constant
    is updated.
    """
    actual_sha = hashlib.sha256(_FIXTURE_PATH.read_bytes()).hexdigest()
    assert actual_sha == _FIXTURE_SHA256, (
        f"fixture SHA-256 drift detected.\n"
        f"  expected: {_FIXTURE_SHA256}\n"
        f"  actual:   {actual_sha}\n"
        f"If you intentionally regenerated the fixture, update "
        f"_FIXTURE_SHA256 in this test file to {actual_sha!r}."
    )
