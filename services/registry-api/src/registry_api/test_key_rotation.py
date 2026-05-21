"""Tests for the Story 11.5 / FR65a key-rotation detector.

Covers AC4 (synchronous + fail-loud rotation detection in registry-api
lifespan). All tests are in-process — no FastAPI lifespan, no
``LifespanManager``. The detector function is called directly with an
in-memory SQLite store + a real :class:`EventLogWriter` writing to
``tmp_path``.

Test matrix (Story 11.5 AC4):

* ``test_detect_emits_event_on_rotation`` — seeded ``KeyFingerprint``
  row + a NEW current key → exactly one ``key.rotated`` event written.
* ``test_detect_is_noop_when_fingerprint_unchanged`` — seeded row with
  current_fp + same current key → no event written.
* ``test_detect_first_boot_emits_event_with_bootstrap_sentinel`` —
  empty table + current key → event written with
  ``previous_key_fingerprint = "0000000000000000"`` (D1).
* ``test_detect_skips_when_current_key_is_none`` — empty table +
  ``current_key=None`` → no event written.
* ``test_detect_emits_exactly_once_per_rotation`` — call detector
  twice with the same key; second call is a no-op (idempotent).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from random import Random

import pytest
import pytest_asyncio
from events import FROZEN_EPOCH, FrozenClock
from events.approval_signing import compute_key_fingerprint
from events.envelope import Actor, EventEnvelope
from events.ids import new_event_id, new_request_id, new_uuid7
from events.log_reader import current_day_path, read_log_lines
from events.payloads import KeyRotatedPayload
from pydantic import SecretStr
from registry_state.adapters.event_log import (  # noqa: IMP001 — test fixture uses registry-state write path for detector; no prod cross-service coupling
    EventLogWriter,
)
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — test fixture builds in-memory SQLite; no prod cross-service coupling
    create_engine,
    get_session,
)
from registry_state.domain.handlers import (  # noqa: IMP001 — PP16 integrated-loop test invokes real materializer; no prod cross-service coupling
    handle_key_rotated,
)
from registry_state.schema import (  # noqa: IMP001 — test fixture seeds KeyFingerprint rows directly; no prod cross-service coupling
    Base,
    KeyFingerprint,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from registry_api.adapters.key_rotation import detect_and_emit_key_rotation

# Two distinct 32-byte keys used across the tests.
_KEY_A_STR = "test-key-A-32-bytes-padded-out-x"
_KEY_B_STR = "test-key-B-32-bytes-padded-out-x"
assert len(_KEY_A_STR.encode("utf-8")) == 32
assert len(_KEY_B_STR.encode("utf-8")) == 32

_KEY_A = SecretStr(_KEY_A_STR)
_KEY_B = SecretStr(_KEY_B_STR)

_FP_A = compute_key_fingerprint(_KEY_A)
_FP_B = compute_key_fingerprint(_KEY_B)
assert _FP_A != _FP_B  # sanity

_BOOTSTRAP_SENTINEL = "0000000000000000"  # Story 11.5 D1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fixed_clock() -> FrozenClock:
    """Frozen clock at FROZEN_EPOCH with mono_ns=1_000_000."""
    return FrozenClock(mono_ns=1_000_000, now=FROZEN_EPOCH)


@pytest_asyncio.fixture
async def session_maker_with_schema(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield a session_maker against a fresh SQLite file with all ORM tables.

    The file lives in ``tmp_path`` so tests are fully isolated. The writable
    engine is disposed on teardown so the file handle releases before
    ``tmp_path`` cleanup.
    """
    db_path = tmp_path / "state.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_engine(db_url, read_only=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = get_session(engine)
    try:
        yield sm
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def event_log_writer(
    tmp_path: Path,
    fixed_clock: FrozenClock,
) -> AsyncIterator[EventLogWriter]:
    """Yield a real :class:`EventLogWriter` rooted at ``tmp_path/events``."""
    writer = EventLogWriter(base_dir=tmp_path / "events", clock=fixed_clock)
    try:
        yield writer
    finally:
        await writer.close()


async def _seed_key_fingerprint(
    sm: async_sessionmaker[AsyncSession],
    *,
    fingerprint: str,
    rotated_by: str = "key-rotation-detector",
) -> None:
    """Story 11.5 PP5 — seed the singleton ``key_fingerprint`` row via
    real UPSERT semantics mirroring ``handle_key_rotated``.

    Pre-PP5 the fixture used a plain ``session.add`` INSERT which:

    1. Would PK-violate on a second call within the same test (the
       row is a singleton on ``id="current"``).
    2. Tested an unrealistic flow — production writes go through the
       materializer's ``INSERT ... ON CONFLICT DO UPDATE`` path, not
       a raw INSERT.

    Refactored to use ``sqlite_insert(...).on_conflict_do_update(...)``
    which exactly mirrors ``handle_key_rotated``'s UPSERT semantics.
    """
    async with sm() as session, session.begin():
        stmt = (
            sqlite_insert(KeyFingerprint)
            .values(
                id="current",
                fingerprint=fingerprint,
                rotated_at=FROZEN_EPOCH,
                rotated_by_actor_id=rotated_by,
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "fingerprint": fingerprint,
                    "rotated_at": FROZEN_EPOCH,
                    "rotated_by_actor_id": rotated_by,
                },
            )
        )
        await session.execute(stmt)  # noqa: SW001 — test-only fixture seeding


def _read_emitted_events(events_dir: Path) -> list[dict[str, object]]:
    """Return all envelopes written to today's JSONL file (in test == FROZEN_EPOCH)."""
    log_path = current_day_path(events_dir, FROZEN_EPOCH)
    if not log_path.exists():
        return []
    return [dict(env) for env in read_log_lines(log_path)]


# ---------------------------------------------------------------------------
# AC4 tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_emits_event_on_rotation(
    tmp_path: Path,
    fixed_clock: FrozenClock,
    session_maker_with_schema: async_sessionmaker[AsyncSession],
    event_log_writer: EventLogWriter,
) -> None:
    """Story 11.5 AC4: seeded fingerprint A + current key B → ``key.rotated`` emitted.

    Verifies the rotation case: prior fingerprint differs from current
    fingerprint → exactly one event written, with both fingerprints
    matching their expected values and ``actor_id =
    "key-rotation-detector"`` (D4).
    """
    await _seed_key_fingerprint(session_maker_with_schema, fingerprint=_FP_A)

    await detect_and_emit_key_rotation(
        current_key=_KEY_B,
        session_maker=session_maker_with_schema,
        event_log_writer=event_log_writer,
        clock=fixed_clock,
    )
    # Flush writer so the line is visible to the reader.
    await event_log_writer.close()

    events = _read_emitted_events(tmp_path / "events")
    assert len(events) == 1, f"expected exactly 1 event, got {len(events)}"
    env = events[0]
    assert env["type"] == "key.rotated"
    payload = env["payload"]
    assert isinstance(payload, dict)
    assert payload["previous_key_fingerprint"] == _FP_A
    assert payload["new_key_fingerprint"] == _FP_B
    assert payload["actor_id"] == "key-rotation-detector"


@pytest.mark.asyncio
async def test_detect_is_noop_when_fingerprint_unchanged(
    tmp_path: Path,
    fixed_clock: FrozenClock,
    session_maker_with_schema: async_sessionmaker[AsyncSession],
    event_log_writer: EventLogWriter,
) -> None:
    """Story 11.5 AC4: seeded fingerprint A + current key A → NO event emitted.

    Equality of prior + current fingerprints is the dedup invariant.
    Restarting registry-api with the same key MUST NOT produce duplicate
    ``key.rotated`` events.
    """
    await _seed_key_fingerprint(session_maker_with_schema, fingerprint=_FP_A)

    await detect_and_emit_key_rotation(
        current_key=_KEY_A,
        session_maker=session_maker_with_schema,
        event_log_writer=event_log_writer,
        clock=fixed_clock,
    )
    await event_log_writer.close()

    events = _read_emitted_events(tmp_path / "events")
    assert events == [], "no event must be emitted when fingerprint is unchanged"


@pytest.mark.asyncio
async def test_detect_first_boot_emits_event_with_bootstrap_sentinel(
    tmp_path: Path,
    fixed_clock: FrozenClock,
    session_maker_with_schema: async_sessionmaker[AsyncSession],
    event_log_writer: EventLogWriter,
) -> None:
    """Story 11.5 AC4 / D1: empty table + current key → emit with sentinel.

    First-boot case: no prior ``KeyFingerprint`` row exists. The detector
    emits ``key.rotated`` with
    ``previous_key_fingerprint = "0000000000000000"`` (16 zero-hex chars,
    collision probability 2⁻⁶⁴) and ``new_key_fingerprint`` set to the
    current key's fingerprint. The ``previous != new`` invariant in
    ``KeyRotatedPayload`` holds because no real key has all-zero SHA-256.
    """
    # No seeding — table is empty.
    await detect_and_emit_key_rotation(
        current_key=_KEY_A,
        session_maker=session_maker_with_schema,
        event_log_writer=event_log_writer,
        clock=fixed_clock,
    )
    await event_log_writer.close()

    events = _read_emitted_events(tmp_path / "events")
    assert len(events) == 1, f"expected exactly 1 event, got {len(events)}"
    env = events[0]
    assert env["type"] == "key.rotated"
    payload = env["payload"]
    assert isinstance(payload, dict)
    assert payload["previous_key_fingerprint"] == _BOOTSTRAP_SENTINEL, (
        f"first-boot sentinel must be {_BOOTSTRAP_SENTINEL!r}; "
        f"got {payload['previous_key_fingerprint']!r}"
    )
    assert payload["new_key_fingerprint"] == _FP_A


@pytest.mark.asyncio
async def test_detect_skips_when_current_key_is_none(
    tmp_path: Path,
    fixed_clock: FrozenClock,
    session_maker_with_schema: async_sessionmaker[AsyncSession],
    event_log_writer: EventLogWriter,
) -> None:
    """Story 11.5 AC4: ``current_key=None`` → no-op + structured log.

    Operator deliberately unset ``OPERATOR_HMAC_KEY``; signing is
    disabled. Pre-existing rotation events remain verifiable via
    ``just verify-approval --key-file PATH``. The detector must NOT
    emit a no-op rotation event for the "signing disabled" case.
    """
    # No seeding — table is empty.
    await detect_and_emit_key_rotation(
        current_key=None,
        session_maker=session_maker_with_schema,
        event_log_writer=event_log_writer,
        clock=fixed_clock,
    )
    await event_log_writer.close()

    events = _read_emitted_events(tmp_path / "events")
    assert events == [], "no event must be emitted when current_key is None"


@pytest.mark.asyncio
async def test_detect_emits_exactly_once_per_rotation(
    tmp_path: Path,
    fixed_clock: FrozenClock,
    session_maker_with_schema: async_sessionmaker[AsyncSession],
    event_log_writer: EventLogWriter,
) -> None:
    """Story 11.5 AC4 + PP16: idempotency — exactly-once across full
    detector → materializer → detector loop.

    Simulates the realistic flow:
      1. First call: empty log + empty table + key A → emit bootstrap
         event via the detector.
      2. Materializer ``handle_key_rotated`` UPSERTs the
         ``KeyFingerprint`` row from the just-emitted event (PP16:
         pre-PP16 the test shortcut this with a direct INSERT, which
         bypassed the materializer; now the test exercises the full
         integrated loop).
      3. Second call: log has the rotation event + row exists with
         fingerprint A + current key A → no-op (PD1: detector reads
         the log first, sees fingerprint A, equal to current_fp ⇒ no
         emit).

    The second call must NOT emit a duplicate event. After both calls
    exactly one event is in the log.
    """
    # First call: first-boot path → emits one event.
    await detect_and_emit_key_rotation(
        current_key=_KEY_A,
        session_maker=session_maker_with_schema,
        event_log_writer=event_log_writer,
        clock=fixed_clock,
    )

    # PP16 — invoke the REAL materializer between detector calls instead
    # of the _seed_key_fingerprint shortcut. We construct a fresh
    # envelope that mirrors the detector's emission (same fingerprints,
    # same actor) and feed it to ``handle_key_rotated`` so the UPSERT
    # path is exercised end-to-end.
    rng_replay = Random(0xFEEDFACE)
    replay_envelope = EventEnvelope.create(
        event_id=new_event_id(clock=fixed_clock, rng=rng_replay),
        type="key.rotated",
        schema_version="1.1.0",
        emitted_at=fixed_clock.now(),
        emitted_at_monotonic_ns=fixed_clock.monotonic_ns(),
        actor=Actor(kind="system", id="key-rotation-detector"),
        payload=KeyRotatedPayload(
            previous_key_fingerprint=_BOOTSTRAP_SENTINEL,
            new_key_fingerprint=_FP_A,
            rotated_at=fixed_clock.now(),
            actor_id="key-rotation-detector",
        ),
        trace_id=new_uuid7(clock=fixed_clock, rng=rng_replay),
        request_id=new_request_id(clock=fixed_clock, rng=rng_replay),
    )
    async with session_maker_with_schema() as session, session.begin():
        await handle_key_rotated(session, replay_envelope)

    # Second call: same key → no-op.
    await detect_and_emit_key_rotation(
        current_key=_KEY_A,
        session_maker=session_maker_with_schema,
        event_log_writer=event_log_writer,
        clock=fixed_clock,
    )
    await event_log_writer.close()

    events = _read_emitted_events(tmp_path / "events")
    assert len(events) == 1, (
        f"exactly one event expected across two calls; got {len(events)} (idempotency broken)"
    )


# ---------------------------------------------------------------------------
# NFR-S10 — key isolation in structlog output (no key bytes in logs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_never_logs_key_value(
    tmp_path: Path,
    fixed_clock: FrozenClock,
    session_maker_with_schema: async_sessionmaker[AsyncSession],
    event_log_writer: EventLogWriter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """NFR-S10: structlog/stdlib logs MUST NOT contain the key bytes.

    Fingerprint (one-way SHA-256 truncation) is OK; byte count is OK; the
    raw key bytes are forbidden. Run the full rotation path (which emits
    a structured log line via ``_log.info``) under ``caplog`` capture and
    grep the captured records for the key sentinel.
    """
    # Story 11.5 PP11 — canary trimmed to exactly 32 bytes for symmetry
    # with AC8 canary `"CANARY-KEY-NEVER-LOG-X-32-BYTES!"`.
    canary_key_str = "NFR-S10-DETECTOR-CANARY-32-BYTES"
    assert len(canary_key_str.encode("utf-8")) == 32
    canary_key = SecretStr(canary_key_str)

    # Trigger first-boot emit path so both the structured log + the event
    # appendage paths run.
    with caplog.at_level("INFO", logger="registry_api.adapters.key_rotation"):
        await detect_and_emit_key_rotation(
            current_key=canary_key,
            session_maker=session_maker_with_schema,
            event_log_writer=event_log_writer,
            clock=fixed_clock,
        )
    await event_log_writer.close()

    # The captured log message MUST NOT include the canary substring.
    for record in caplog.records:
        assert canary_key_str not in record.getMessage(), (
            "structlog record leaked OPERATOR_HMAC_KEY bytes — NFR-S10 violation"
        )

    # And the resulting JSONL event log MUST NOT include the canary either.
    log_path = current_day_path(tmp_path / "events", FROZEN_EPOCH)
    raw_log = log_path.read_text(encoding="utf-8")
    assert canary_key_str not in raw_log, "event log leaked the key bytes"


# ---------------------------------------------------------------------------
# AC4 — sanity: rotated_at on emitted event matches clock.now()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_emit_uses_clock_now_for_rotated_at(
    tmp_path: Path,
    session_maker_with_schema: async_sessionmaker[AsyncSession],
) -> None:
    """Sanity: rotated_at on the emitted payload equals clock.now().

    Uses an explicit clock-now value (not FROZEN_EPOCH) so we can prove
    the detector reads from the injected clock rather than a wall-clock
    side-effect (Story 2.1 clock-injection convention).
    """
    explicit_now = datetime(2026, 5, 21, 14, 30, 0, tzinfo=UTC)
    clk = FrozenClock(mono_ns=42_000, now=explicit_now)
    writer = EventLogWriter(base_dir=tmp_path / "events", clock=clk)
    try:
        await detect_and_emit_key_rotation(
            current_key=_KEY_A,
            session_maker=session_maker_with_schema,
            event_log_writer=writer,
            clock=clk,
        )
    finally:
        await writer.close()

    log_path = current_day_path(tmp_path / "events", explicit_now)
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    env = json.loads(lines[0])
    payload = env["payload"]
    # rotated_at serialises to canonical ms-truncated ISO + Z.
    assert payload["rotated_at"].startswith("2026-05-21T14:30:00")


# ---------------------------------------------------------------------------
# Story 11.5 PD1 — event-log SSoT lookup tests
# ---------------------------------------------------------------------------


async def _append_synthetic_key_rotated_envelope(
    writer: EventLogWriter,
    *,
    clock: FrozenClock,
    rng: Random,
    previous_fp: str,
    new_fp: str,
) -> EventEnvelope:
    """Append a synthetic ``key.rotated`` envelope to the JSONL log
    WITHOUT materializing it via ``handle_key_rotated``.

    Simulates the cross-restart race PD1 closes: the rotation detector
    emitted an event that hit the JSONL log but the subscriber-
    materializer had not yet committed the corresponding SQLite row.
    """
    payload = KeyRotatedPayload(
        previous_key_fingerprint=previous_fp,
        new_key_fingerprint=new_fp,
        rotated_at=clock.now(),
        actor_id="key-rotation-detector",
    )
    envelope = EventEnvelope.create(
        event_id=new_event_id(clock=clock, rng=rng),
        type="key.rotated",
        schema_version="1.1.0",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind="system", id="key-rotation-detector"),
        payload=payload,
        trace_id=new_uuid7(clock=clock, rng=rng),
        request_id=new_request_id(clock=clock, rng=rng),
    )
    await writer.append(envelope)
    return envelope


@pytest.mark.asyncio
async def test_detect_reads_most_recent_rotation_from_event_log_when_sqlite_lags(
    tmp_path: Path,
    fixed_clock: FrozenClock,
    session_maker_with_schema: async_sessionmaker[AsyncSession],
    event_log_writer: EventLogWriter,
) -> None:
    """Story 11.5 PD1: detector reads JSONL log first; no duplicate
    emission when SQLite lags behind the log (cross-restart race).

    Pre-PD1 the detector consulted only SQLite. If registry-api
    restarted faster than the subscriber-materializer could process
    the prior boot's ``key.rotated`` event, the detector saw an empty
    table + a current key and emitted a DUPLICATE first-boot event.

    Post-PD1 the detector reads the JSONL log first (the SSoT per
    arch_refs P2-I3); SQLite is the snapshot-restored fallback. We
    simulate the race by appending a ``key.rotated`` envelope to the
    log WITHOUT materializing it via ``handle_key_rotated``, then
    invoke the detector with the corresponding current key. The
    detector must observe the log's fingerprint, compare equal, and
    emit NO duplicate event.
    """
    # Step 1 — write a prior rotation event to the log via the SAME
    # writer the detector will use. KeyFingerprint table stays EMPTY
    # (simulating subscriber lag).
    rng_seed = Random(0xCAFEBABE)
    await _append_synthetic_key_rotated_envelope(
        event_log_writer,
        clock=fixed_clock,
        rng=rng_seed,
        previous_fp=_BOOTSTRAP_SENTINEL,
        new_fp=_FP_A,
    )
    # Sanity: nothing materialized in SQLite — the lag is real.
    async with session_maker_with_schema() as session:
        from sqlalchemy import select as _select

        result = await session.execute(_select(KeyFingerprint))
        assert result.scalar_one_or_none() is None, (
            "test invariant: SQLite must be empty so PD1's log-first lookup is exercised"
        )

    # Step 2 — invoke the detector with the SAME key the log already
    # records. PD1: detector reads the log first, sees _FP_A, compares
    # equal to compute_key_fingerprint(_KEY_A) → no-op.
    await detect_and_emit_key_rotation(
        current_key=_KEY_A,
        session_maker=session_maker_with_schema,
        event_log_writer=event_log_writer,
        clock=fixed_clock,
    )
    await event_log_writer.close()

    events = _read_emitted_events(tmp_path / "events")
    assert len(events) == 1, (
        f"PD1: detector must dedup against the JSONL log; got {len(events)} events "
        "(duplicate emission — exactly-once invariant broken)"
    )
    assert events[0]["payload"]["new_key_fingerprint"] == _FP_A  # type: ignore[index]


@pytest.mark.asyncio
async def test_detect_handles_event_log_missing_falls_back_to_sqlite(
    tmp_path: Path,
    fixed_clock: FrozenClock,
    session_maker_with_schema: async_sessionmaker[AsyncSession],
    event_log_writer: EventLogWriter,
) -> None:
    """Story 11.5 PD1: detector falls back to SQLite ``KeyFingerprint``
    when the JSONL log has no ``key.rotated`` events.

    Backwards-compatibility path for snapshot-restored deployments
    where the JSONL log has been truncated/rotated out but the SQLite
    projection survives. Without this fallback the detector would
    treat the missing log as "first boot" and emit a duplicate
    bootstrap event.
    """
    # Seed SQLite (snapshot-restore simulation) and ensure log dir is
    # empty of rotation events. The event_log_writer fixture creates
    # the base_dir but does not write to it yet.
    await _seed_key_fingerprint(session_maker_with_schema, fingerprint=_FP_A)
    # Sanity: log dir has no .jsonl files (no prior rotation events).
    log_dir = tmp_path / "events"
    jsonl_files = list(log_dir.glob("*.jsonl"))
    assert jsonl_files == [], (
        f"test invariant: log dir must be empty for fallback to fire; got {jsonl_files}"
    )

    await detect_and_emit_key_rotation(
        current_key=_KEY_A,
        session_maker=session_maker_with_schema,
        event_log_writer=event_log_writer,
        clock=fixed_clock,
    )
    await event_log_writer.close()

    events = _read_emitted_events(tmp_path / "events")
    assert events == [], (
        f"PD1 fallback: SQLite fingerprint A + current key A → no-op; got {len(events)} events"
    )


# ---------------------------------------------------------------------------
# Story 11.5 PP3 — current_key=None warning when prior fingerprint exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_warns_when_key_unset_but_prior_fingerprint_exists(
    tmp_path: Path,
    fixed_clock: FrozenClock,
    session_maker_with_schema: async_sessionmaker[AsyncSession],
    event_log_writer: EventLogWriter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Story 11.5 PP3: current_key=None + prior fingerprint exists → WARNING.

    Pre-PP3 the detector logged INFO and silently returned in the
    ``current_key=None`` branch regardless of prior state. PP3 promotes
    the skip to WARNING (with ``prior_fp_exists=True``) when a prior
    rotation has been recorded — operators must notice the
    inconsistent state of "previously rotated keys but signing now
    disabled".
    """
    # Seed a prior fingerprint via SQLite (we could also exercise the
    # event-log path; SQLite is sufficient since PP3 uses the same
    # _read_prior_fingerprint helper that PD1 introduced).
    await _seed_key_fingerprint(session_maker_with_schema, fingerprint=_FP_A)

    with caplog.at_level(logging.WARNING, logger="registry_api.adapters.key_rotation"):
        await detect_and_emit_key_rotation(
            current_key=None,
            session_maker=session_maker_with_schema,
            event_log_writer=event_log_writer,
            clock=fixed_clock,
        )
    await event_log_writer.close()

    # No event emitted (current_key=None still short-circuits).
    events = _read_emitted_events(tmp_path / "events")
    assert events == [], "current_key=None must not emit even when prior fingerprint exists"

    # Exactly one WARNING record from the detector's PP3 branch.
    warning_records = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name == "registry_api.adapters.key_rotation"
        and "prior_fp_exists=True" in r.getMessage()
    ]
    assert len(warning_records) == 1, (
        f"PP3: exactly one WARNING expected when current_key=None and prior_fp exists; "
        f"got {len(warning_records)} (records: {[r.getMessage() for r in caplog.records]})"
    )


# ---------------------------------------------------------------------------
# Story 11.5 PP7 — RNG entropy: distinct event_ids under same FrozenClock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_produces_distinct_event_ids_under_same_frozen_clock(
    tmp_path: Path,
    fixed_clock: FrozenClock,
    session_maker_with_schema: async_sessionmaker[AsyncSession],
) -> None:
    """Story 11.5 PP7: two consecutive same-FrozenClock detector calls
    that BOTH emit MUST produce DISTINCT event_ids.

    Pre-PP7 the detector seeded a per-emit ``Random(clock.monotonic_ns())``
    which, under a FrozenClock with a fixed ``mono_ns``, produced
    IDENTICAL event_ids for two same-clock emissions. The PD1 no-op
    short-circuit masked the defect in production (the second call
    would short-circuit before reaching the RNG seed branch), but the
    test catches the latent same-clock-multi-emit case.

    PP7 mixes ``os.urandom(8)``'s hash into the seed so distinct calls
    yield distinct ids even under a FrozenClock.
    """
    # First emit: empty log + empty table + key A → bootstrap event.
    writer_1 = EventLogWriter(base_dir=tmp_path / "events", clock=fixed_clock)
    try:
        await detect_and_emit_key_rotation(
            current_key=_KEY_A,
            session_maker=session_maker_with_schema,
            event_log_writer=writer_1,
            clock=fixed_clock,
        )
    finally:
        await writer_1.close()

    # Force a fresh state for the second emit: a separate session_maker
    # with empty schema + a separate event-log directory so PD1 doesn't
    # short-circuit on the just-written rotation event.
    db_path = tmp_path / "second.sqlite3"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_engine(db_url, read_only=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = get_session(engine)
    try:
        writer_2 = EventLogWriter(base_dir=tmp_path / "events2", clock=fixed_clock)
        try:
            await detect_and_emit_key_rotation(
                current_key=_KEY_A,
                session_maker=sm,
                event_log_writer=writer_2,
                clock=fixed_clock,
            )
        finally:
            await writer_2.close()
    finally:
        await engine.dispose()

    # Read both event_ids and assert distinctness.
    events_1 = _read_emitted_events(tmp_path / "events")
    events_2 = _read_emitted_events(tmp_path / "events2")
    assert len(events_1) == 1
    assert len(events_2) == 1
    eid_1 = events_1[0]["event_id"]
    eid_2 = events_2[0]["event_id"]
    assert eid_1 != eid_2, (
        f"PP7: two same-FrozenClock emissions produced identical event_ids "
        f"({eid_1!r}); RNG entropy must include per-invocation source so the "
        "exactly-once-but-distinct invariant holds for the (latent) multi-emit case"
    )
