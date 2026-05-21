"""Story 11.5 AC8 — Epic 11 acceptance gate: HMAC key isolation.

Grep-based proof that ``OPERATOR_HMAC_KEY`` never appears in any
event log, snapshot, registry-state database, or structlog output
regardless of which path exercises the signing or rotation logic.

All four tests boot the full registry-api in-process via
``asgi_lifespan.LifespanManager`` (no Docker / compose required) with a
canary key string that is unmistakably identifiable. They exercise the
real signing path (POST /v1/tasks/.../decisions) AND the key-rotation
detection path (lifespan startup emits ``key.rotated`` when no prior
``KeyFingerprint`` row exists). After the app shuts down the tests grep
JSONL files, the raw SQLite file bytes, and captured structlog events for
the canary string.

Design decisions:

* **In-process, not Docker (AC8 alternative path)**: the spec allowed a
  lighter alternative when full-stack bootstrap is too heavy. Full-stack
  requires compose orchestration (registry-state subscriber + clawhip +
  telegram-gateway) — that is the compose-based ``test_journey_*.py``
  suite. For the *key isolation* gate we need: registry-api writes JSONL
  + SQLite; we can trigger both paths without the full subscriber stack.
  The isolation invariant only requires the *emitting* side to not leak
  the key — reading/materializing via registry-state subscriber is
  already covered by Story 11.4 PP11's ``test_verify_approval_never_logs_key_value``.

* **Snapshot test**: uses ``registry_state.domain.snapshots.SnapshotPolicy``
  directly against a writable engine to force-capture a snapshot row into
  the same SQLite file the registry-api uses. The snapshot payload contains
  task/session state from registry-state — if the key bytes somehow reached
  those tables, the snapshot captures them. We use a writable engine
  (not the read-only one registry-api wires) for the snapshot write.

* **``@pytest.mark.slow`` (D5)**: lifespan startup + HTTP round-trips are
  the heavyweight part. Matches Story 11.4 PP14 precedent for bounded-memory
  and key-isolation tests.

All four tests are gate-blocking for Epic 11 even under ``slow`` marking:
``uv run pytest -q tests/integration/test_hmac_key_isolation.py -m slow``
MUST be exit 0 before any Story-12+ work merges.

Canary: ``"CANARY-KEY-NEVER-LOG-X-32-BYTES!"`` (32 bytes exactly —
confirmed at module load).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncGenerator
from pathlib import Path
from random import Random

import pytest
import pytest_asyncio
import structlog.testing
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, Actor, FrozenClock, new_event_id, new_uuid7
from events.approval_signing import compute_key_fingerprint
from events.ids import new_request_id
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from registry_api.app import build_app
from registry_api.settings import ApprovalSigningSettings
from registry_state.adapters.sqlite_store import create_engine, get_session
from registry_state.domain.event_types import ensure_registered
from registry_state.domain.snapshots import SnapshotPolicy
from registry_state.schema import Base, Task
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ---------------------------------------------------------------------------
# Canary key — 32 bytes exactly (assert at import time so test authoring
# mistakes are caught immediately, not silently during execution).
# ---------------------------------------------------------------------------

_CANARY_KEY_STR = "CANARY-KEY-NEVER-LOG-X-32-BYTES!"
assert len(_CANARY_KEY_STR.encode("utf-8")) == 32, (
    f"canary key must be exactly 32 bytes; got {len(_CANARY_KEY_STR.encode('utf-8'))}"
)
_CANARY_KEY = SecretStr(_CANARY_KEY_STR)

# Canary sub-strings to grep for (in addition to the full key). These cover
# partial-leak scenarios where e.g. the JSON encoder splits or truncates.
# We use the full string; sub-string checks are implicit via ``in``.
_CANARY_FP = compute_key_fingerprint(_CANARY_KEY)  # fingerprint is OK in logs
assert _CANARY_KEY_STR not in _CANARY_FP  # sanity: fingerprint doesn't contain key

# Seeded task IDs for the signing test. Must match the UUIDv7 format that
# registry-api validates: ``^t-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$``
# Generated via: new_task_id(clock=FrozenClock(1_000_000, now=FROZEN_EPOCH), rng=Random(42))
_TID = "t-019b76da-a800-7d79-b1a3-7f31801c6706"
_FROZEN_MONO_NS = 1_000_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _seed_tables_with_task(db_url: str) -> None:
    """Create all ORM tables + one task row so the /decisions endpoint is reachable."""
    engine = create_engine(db_url, read_only=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            Task.__table__.insert(),  # type: ignore[attr-defined]
            {
                "id": _TID,
                "status": "plan_ready",
                "title": "canary task",
                "actor_kind": "operator",
                "actor_id": "test-op",
                "created_at": FROZEN_EPOCH,
                "updated_at": FROZEN_EPOCH,
            },
        )
    await engine.dispose()


def _read_all_jsonl(events_dir: Path) -> str:
    """Return concatenated text of all JSONL files in events_dir."""
    chunks: list[str] = []
    for f in sorted(events_dir.rglob("*.jsonl")):
        chunks.append(f.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def _read_sqlite_raw(db_path: Path) -> bytes:
    """Return the raw bytes of the SQLite file (for canary grep)."""
    return db_path.read_bytes()


def _read_sqlite_text_dump(db_path: Path) -> str:
    """Return all readable text columns from the SQLite file via sqlite3.

    Dumps each table as JSON so the grep covers payload_json, event types,
    actor_id fields, etc. without parsing the binary SQLite format.
    """
    parts: list[str] = []
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cur.fetchall()]
        for tbl in tables:
            cur.execute(f"SELECT * FROM {tbl}")  # noqa: S608 — test-only
            rows = cur.fetchall()
            parts.append(json.dumps(rows))
    finally:
        conn.close()
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Shared async fixture: in-process registry-api with canary key
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def canary_client(
    tmp_path: Path,
) -> AsyncGenerator[tuple[AsyncClient, Path, Path], None]:
    """Boot registry-api in-process with the canary key.

    Yields ``(client, events_dir, db_path)`` so tests can grep the
    written artefacts after the lifespan exits.

    The canary key is injected via ``signing_settings`` (not env-var) so
    only this fixture sees it — no risk of leaking to sibling processes.
    """
    ensure_registered()
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables_with_task(db_url)

    events_dir = tmp_path / "events"
    clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)

    signing_settings = ApprovalSigningSettings(operator_hmac_key=_CANARY_KEY)
    app = build_app(
        base_dir=events_dir,
        db_url=db_url,
        clock=clock,
        signing_settings=signing_settings,
    )

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app),
            base_url="http://testserver",
        ) as client,
    ):
        # POST an approval decision so the signing path runs + events land
        # on disk. The task status is plan_ready which the lifecycle allows
        # for the "approve" action.
        r = await client.post(
            f"/v1/tasks/{_TID}/decisions",
            json={"action": "approve"},
        )
        # Accept 202 or 400 (e.g. lifecycle check) — what matters is that
        # the signing code ran (it runs before the lifecycle gate in the
        # handler). But status=plan_ready should give 202.
        assert r.status_code in (202, 400), f"Unexpected status {r.status_code}: {r.text}"
        yield client, events_dir, db_path


# ---------------------------------------------------------------------------
# AC8 tests — all @pytest.mark.slow (D5)
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_operator_hmac_key_never_appears_in_event_log(
    canary_client: tuple[AsyncClient, Path, Path],
) -> None:
    """Epic 11 AC8 / NFR-S10: OPERATOR_HMAC_KEY bytes MUST NOT appear in JSONL.

    Greps all ``.jsonl`` files written by registry-api's ``EventLogWriter``
    (after the full lifespan exits + writer is flushed/closed) for the
    canary key string. Two events land on disk: ``key.rotated`` (first-boot
    emission from the rotation detector) and the paired ``approval.granted``
    + ``task.approval_signed`` from the POST /decisions call. None of them
    may contain the raw key bytes.
    """
    _, events_dir, _ = canary_client

    jsonl_content = _read_all_jsonl(events_dir)
    assert jsonl_content, "no JSONL events written — fixture may have failed"

    assert _CANARY_KEY_STR not in jsonl_content, (
        "OPERATOR_HMAC_KEY bytes found in JSONL event log — NFR-S10 VIOLATION.\n"
        f"JSONL content (first 500 chars): {jsonl_content[:500]!r}"
    )
    # Sanity: the fingerprint IS present (proves we actually wrote events).
    assert _CANARY_FP in jsonl_content, (
        "key fingerprint not found in event log — expected key.rotated event with fingerprint; "
        f"check fixture. JSONL: {jsonl_content[:500]!r}"
    )


@pytest.mark.slow
@pytest.mark.asyncio
async def test_operator_hmac_key_never_appears_in_snapshot(
    tmp_path: Path,
) -> None:
    """Epic 11 AC8 / NFR-S10: OPERATOR_HMAC_KEY bytes MUST NOT appear in snapshots.

    Boots registry-api with the canary key, exercises the signing + rotation
    path (lifespan emits ``key.rotated``), then forces a snapshot capture via
    ``SnapshotPolicy.capture`` against the same SQLite DB. The snapshot's
    ``payload_json`` column in the ``snapshots`` table MUST NOT contain the
    canary key.
    """
    ensure_registered()
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables_with_task(db_url)

    events_dir = tmp_path / "events"
    clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)

    signing_settings = ApprovalSigningSettings(operator_hmac_key=_CANARY_KEY)
    app = build_app(
        base_dir=events_dir,
        db_url=db_url,
        clock=clock,
        signing_settings=signing_settings,
    )

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app),
            base_url="http://testserver",
        ) as client,
    ):
        # POST an approval so tasks/events are in the DB.
        await client.post(
            f"/v1/tasks/{_TID}/decisions",
            json={"action": "approve"},
        )

    # Force a snapshot capture using a writable engine against the same DB.
    write_engine = create_engine(db_url, read_only=False)
    write_sm: async_sessionmaker[AsyncSession] = get_session(write_engine)
    policy = SnapshotPolicy(session_maker=write_sm, clock=clock, interval=1)

    # Build a minimal envelope as the cursor anchor for the snapshot.
    rng = Random(99)
    fake_envelope_for_cursor = _make_fake_envelope(clock=clock, rng=rng)
    snapshot_id = await policy.capture(fake_envelope_for_cursor)
    await write_engine.dispose()

    assert snapshot_id, "snapshot must have been captured"

    # Grep the raw SQLite file bytes for the canary string.
    db_raw = _read_sqlite_raw(db_path)
    assert _CANARY_KEY_STR.encode("utf-8") not in db_raw, (
        "OPERATOR_HMAC_KEY bytes found in SQLite snapshot payload — NFR-S10 VIOLATION"
    )

    # Belt-and-braces: text dump of all table rows.
    text_dump = _read_sqlite_text_dump(db_path)
    assert _CANARY_KEY_STR not in text_dump, (
        "OPERATOR_HMAC_KEY string found in SQLite text dump — NFR-S10 VIOLATION"
    )


@pytest.mark.slow
@pytest.mark.asyncio
async def test_operator_hmac_key_never_appears_in_registry_state_db(
    canary_client: tuple[AsyncClient, Path, Path],
) -> None:
    """Epic 11 AC8 / NFR-S10: OPERATOR_HMAC_KEY bytes MUST NOT appear in SQLite.

    After registry-api's lifespan exits, greps the raw SQLite file (binary
    search on the bytes) and also runs a text-dump of all table rows.
    Covers: the ``key.rotated`` event row's ``payload_json`` column, the
    ``key_fingerprint`` row, the ``events`` table's ``payload_json`` column
    for ``task.approval_signed`` (which contains the HMAC hex output — safe,
    because the HMAC is a one-way transform, not the key itself), and all
    other columns.
    """
    _, _, db_path = canary_client

    # Raw bytes search.
    db_raw = _read_sqlite_raw(db_path)
    assert _CANARY_KEY_STR.encode("utf-8") not in db_raw, (
        "OPERATOR_HMAC_KEY raw bytes found in registry-state SQLite — NFR-S10 VIOLATION"
    )

    # Text dump search (covers JSON payloads stored as text).
    text_dump = _read_sqlite_text_dump(db_path)
    assert _CANARY_KEY_STR not in text_dump, (
        "OPERATOR_HMAC_KEY string found in registry-state SQLite text dump — NFR-S10 VIOLATION"
    )


@pytest.mark.slow
@pytest.mark.asyncio
async def test_operator_hmac_key_never_appears_in_structlog_output(
    tmp_path: Path,
) -> None:
    """Epic 11 AC8 / NFR-S10: OPERATOR_HMAC_KEY MUST NOT appear in structlog events.

    Captures ALL structlog events emitted during the full lifespan (startup,
    key-rotation detection, the signing handler path, shutdown) via
    ``structlog.testing.capture_logs()``. Greps every event dict's values
    for the canary key string.

    Note: ``structlog.testing.capture_logs()`` intercepts events bound for
    the structlog pipeline before they reach processors — this is the
    authoritative capture mechanism for structured logs (vs ``caplog`` which
    captures stdlib log records and may miss structlog events depending on
    configuration, per Story 11.1 P1-M1).
    """
    ensure_registered()
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables_with_task(db_url)

    events_dir = tmp_path / "events"
    clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)

    signing_settings = ApprovalSigningSettings(operator_hmac_key=_CANARY_KEY)
    app = build_app(
        base_dir=events_dir,
        db_url=db_url,
        clock=clock,
        signing_settings=signing_settings,
    )

    with structlog.testing.capture_logs() as cap:
        async with LifespanManager(app) as manager:
            async with AsyncClient(
                transport=ASGITransport(app=manager.app),
                base_url="http://testserver",
            ) as client:
                r = await client.post(
                    f"/v1/tasks/{_TID}/decisions",
                    json={"action": "approve"},
                )
                assert r.status_code in (202, 400)

    # Grep every structlog event dict for the canary key string.
    leaking_events: list[dict[str, object]] = []
    for event_dict in cap:
        for value in event_dict.values():
            if _CANARY_KEY_STR in str(value):
                leaking_events.append(event_dict)
                break

    assert not leaking_events, (
        f"OPERATOR_HMAC_KEY bytes found in {len(leaking_events)} structlog event(s) "
        f"— NFR-S10 VIOLATION.\nLeaking events: {leaking_events[:3]!r}"
    )

    # Sanity: at least some structlog events were captured (proves the
    # capture_logs() context was active during the relevant paths).
    assert len(cap) >= 1, (
        "No structlog events captured — check that structlog is configured for "
        "this process and that capture_logs() is wrapping the correct scope."
    )


# ---------------------------------------------------------------------------
# Helper — build a minimal EventEnvelope for snapshot cursor anchor
# ---------------------------------------------------------------------------


def _make_fake_envelope(
    *,
    clock: FrozenClock,
    rng: Random,
) -> object:
    """Build a minimal ``task.created`` envelope for use as a snapshot cursor.

    SnapshotPolicy.capture() needs an ``EventEnvelope`` as its cursor anchor.
    We use a well-formed ``task.created`` envelope with deterministic IDs.
    """
    from events import EventEnvelope
    from events.payloads import TaskCreatedPayload

    return EventEnvelope.create(
        event_id=new_event_id(clock=clock, rng=rng),
        type="task.created",
        schema_version="1.0.0",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind="operator", id="test-op"),
        payload=TaskCreatedPayload(
            task_id=_TID,
            title="canary task",
        ),
        trace_id=new_uuid7(clock=clock, rng=rng),
        request_id=new_request_id(clock=clock, rng=rng),
    )
