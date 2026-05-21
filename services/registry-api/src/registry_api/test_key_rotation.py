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
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from events import FROZEN_EPOCH, FrozenClock
from events.approval_signing import compute_key_fingerprint
from events.log_reader import current_day_path, read_log_lines
from pydantic import SecretStr
from registry_state.adapters.event_log import (  # noqa: IMP001 — test fixture uses registry-state write path for detector; no prod cross-service coupling
    EventLogWriter,
)
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — test fixture builds in-memory SQLite; no prod cross-service coupling
    create_engine,
    get_session,
)
from registry_state.schema import (  # noqa: IMP001 — test fixture seeds KeyFingerprint rows directly; no prod cross-service coupling
    Base,
    KeyFingerprint,
)
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
    """Insert a singleton ``key_fingerprint`` row with the given fingerprint."""
    async with sm() as session, session.begin():
        row = KeyFingerprint(
            id="current",
            fingerprint=fingerprint,
            rotated_at=FROZEN_EPOCH,
            rotated_by_actor_id=rotated_by,
        )
        session.add(row)  # noqa: SW001 — test-only fixture seeding


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
    """Story 11.5 AC4: idempotency — calling detector twice with same key emits once.

    Simulates the realistic flow:
      1. First call: empty table + key A → emit bootstrap event.
      2. Materializer (mocked here via direct row insert) commits the
         ``KeyFingerprint`` row.
      3. Second call: row exists with fingerprint A + current key A → no-op.

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
    # Simulate materializer commit (registry-state would do this from the
    # subscriber side; tests do it inline here since the detector itself
    # only emits via the event log).
    await _seed_key_fingerprint(session_maker_with_schema, fingerprint=_FP_A)

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
    canary_key_str = "NFR-S10-DETECTOR-CANARY-VAL-32B-X"
    assert len(canary_key_str.encode("utf-8")) == 33
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
