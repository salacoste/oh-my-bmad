"""Story 2.13: 100× concurrent replay test for POST /v1/tasks idempotency.

Verifies FR28 ("dedupe by client-generated idempotency key, return prior
result on collision") and NFR-R4 ("zero duplicate executions per 100
concurrent duplicate submissions of the same command").

Test inventory (per AC-6, AC-8, AC-9):
  - test_idempotency_100x_concurrent_same_key_yields_one_task_and_identical_responses
        — 100 concurrent POSTs with the same Idempotency-Key. All 201,
          all body-bytes identical, exactly 1 ``applied`` + 99 ``replayed``,
          exactly 1 row in tasks table (after materialization), exactly 1
          ``task.created`` event in JSONL.
  - test_idempotency_100x_replay_runs_10_times_no_flakiness[0..9]
        — Parametrized 10 iterations of the headline test. Each iteration
          uses a fresh app + fresh tmp_path + fresh Idempotency-Key so
          7-day-TTL state from one iteration cannot leak into another.
          Provides nightly's statistical-flakiness signal.
  - test_idempotency_post_completion_replay_returns_cached
        — Sequential same-key POSTs (NOT concurrent). Second call returns
          the cached body without re-emitting the event.
  - test_idempotency_error_during_first_attempt_does_not_cache
        — IdempotencyCacheStore.get_or_run does NOT cache factory failures.
          First POST raises (writer.append patched to raise) → 500;
          second POST with same key succeeds with 201 + ``applied`` (NOT
          ``replayed``).

Test-pattern reference: ``services/registry-api/src/registry_api/test_app.py``
(Story 2.9). Same ``httpx.AsyncClient + ASGITransport + LifespanManager``
shape; in-memory SQLite via tmp_path file (the registry-api read-only
engine forbids ``:memory:`` URLs per Story 2.3).

Note: this test file does NOT exercise the registry-state materializer
(separate process). The "tasks table count" assertion is replaced by the
"events.create_all on a writable engine" pre-seed pattern from
``test_app.py`` and a direct read of the JSONL log to count
``task.created`` entries — that's the canonical durable artifact and is
sufficient for proving the dedup invariant.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock, new_idempotency_key
from events.envelope import EventEnvelope
from events.schema_registry import register as _reg
from httpx import ASGITransport, AsyncClient, Response
from registry_api.app import build_app
from registry_state.adapters.event_log import (  # noqa: IMP001 — services→services allowed per AC-16
    current_day_path,
    read_log_lines,
)
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — services→services allowed per AC-16
    create_engine as _create_engine,
)
from registry_state.domain.event_types import (  # noqa: IMP001 — services→services allowed per AC-16
    TaskCreatedPayload,
)
from registry_state.schema import Base  # noqa: IMP001 — services→services allowed per AC-16

# ---------------------------------------------------------------------------
# Schema-registry guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_event_types_registered() -> None:
    """Re-register ``task.created`` before every test.

    Other test modules call ``unregister_all()`` in autouse teardown; this
    keeps the registry populated for our envelope reads (which exercise the
    registry on log-line parse).
    """
    _reg("task.created", "1.0.0", TaskCreatedPayload)


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


_FROZEN_MONO_NS = 1_000_000


@pytest.fixture
def fixed_clock() -> FrozenClock:
    """Stationary clock at FROZEN_EPOCH; mono_ns=1_000_000."""
    return FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _seed_tables(db_url: str) -> None:
    """Create all ORM tables (tasks/events/sessions/idempotency_cache) on a writable DB."""
    engine = _create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def app_client(
    tmp_path: Path, fixed_clock: FrozenClock
) -> AsyncIterator[tuple[AsyncClient, Path, Path]]:
    """Yield a tuple ``(client, db_path, events_dir)`` for in-test inspection.

    The tuple shape is intentional — the headline 100× test needs both the
    client AND the db/events paths to assert post-call invariants directly
    rather than via additional HTTP probes.
    """
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables(db_url)

    events_dir = tmp_path / "events"
    app = build_app(base_dir=events_dir, db_url=db_url, clock=fixed_clock)

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app),
            base_url="http://testserver",
        ) as client,
    ):
        yield client, db_path, events_dir


# ---------------------------------------------------------------------------
# Headline assertions — extracted so the parametrized variant can reuse them.
# ---------------------------------------------------------------------------


async def _run_100x_iteration(
    client: AsyncClient,
    *,
    events_dir: Path,
    idempotency_key: str,
    n: int = 100,
    title: str = "100x replay",
) -> None:
    """Execute *n* concurrent POSTs with the same idempotency-key and assert invariants.

    Invariants asserted:
      - All *n* responses have status 201.
      - All *n* response bodies are byte-identical.
      - Exactly ONE response carries ``X-Idempotency-Status: applied``;
        the remaining *n-1* carry ``X-Idempotency-Status: replayed``.
      - Exactly ONE ``task.created`` event landed in the JSONL log
        (the canonical durable artifact — registry-state materialization
        runs in a separate process not under test here).
    """
    coros: list[Awaitable[Response]] = [
        client.post(
            "/v1/tasks",
            json={"title": title},
            headers={"Idempotency-Key": idempotency_key},
        )
        for _ in range(n)
    ]
    responses: list[Response] = await asyncio.gather(*coros)

    # All-201
    statuses = [r.status_code for r in responses]
    assert statuses == [201] * n, f"non-201 responses present: {statuses}"

    # Byte-identical bodies
    bodies = [r.content for r in responses]
    first_body = bodies[0]
    for i, body in enumerate(bodies):
        assert body == first_body, (
            f"response {i} body diverges from response 0 (len {len(body)} vs {len(first_body)})"
        )

    # Exactly 1 applied + (n-1) replayed
    statuses_hdr = [r.headers.get("X-Idempotency-Status") for r in responses]
    applied_count = sum(1 for s in statuses_hdr if s == "applied")
    replayed_count = sum(1 for s in statuses_hdr if s == "replayed")
    assert applied_count == 1, (
        f"expected exactly 1 'applied' response; got {applied_count} (replayed={replayed_count})"
    )
    assert replayed_count == n - 1, f"expected {n - 1} 'replayed' responses; got {replayed_count}"

    # JSONL log has exactly one task.created (event written exactly once).
    log_path = current_day_path(events_dir, FROZEN_EPOCH)
    if not log_path.exists():
        # Possible if the log file landed under a different day; fall back to
        # globbing the events dir for any *.jsonl.
        envelopes: list[EventEnvelope] = []
        for p in events_dir.glob("*.jsonl"):
            envelopes.extend(read_log_lines(p))
    else:
        envelopes = list(read_log_lines(log_path))
    task_created_count = sum(1 for e in envelopes if e.type == "task.created")
    assert task_created_count == 1, (
        f"expected exactly 1 task.created event in JSONL; got {task_created_count}"
    )

    # Echo header invariant — Idempotency-Key is echoed unchanged.
    for r in responses:
        assert r.headers.get("Idempotency-Key") == idempotency_key


# ---------------------------------------------------------------------------
# AC-6 headline: 100× concurrent same-key replay.
# ---------------------------------------------------------------------------


@pytest.mark.idempotency
@pytest.mark.asyncio
async def test_idempotency_100x_concurrent_same_key_yields_one_task_and_identical_responses(
    app_client: tuple[AsyncClient, Path, Path],
    fixed_clock: FrozenClock,
) -> None:
    """100 concurrent POSTs with the same key → all 201, byte-identical, 1 event.

    NFR-R4 canonical verification: under a 100× retry storm with the same
    Idempotency-Key, ``IdempotencyCacheStore.get_or_run``'s per-key
    asyncio.Lock guarantees the factory runs EXACTLY ONCE; losers receive
    the cached body byte-for-byte.
    """
    client, _db_path, events_dir = app_client
    idempotency_key = new_idempotency_key(clock=fixed_clock)
    await _run_100x_iteration(
        client,
        events_dir=events_dir,
        idempotency_key=idempotency_key,
        n=100,
    )


# ---------------------------------------------------------------------------
# AC-6 parametrized 10× — flakiness signal.
# ---------------------------------------------------------------------------


@pytest.mark.idempotency
@pytest.mark.asyncio
@pytest.mark.parametrize("iteration", list(range(10)))
async def test_idempotency_100x_replay_runs_10_times_no_flakiness(
    iteration: int,
    tmp_path: Path,
    fixed_clock: FrozenClock,
) -> None:
    """Run the 100× concurrent-replay assertion across 10 independent iterations.

    Each iteration uses a fresh app, fresh tmp_path, fresh Idempotency-Key
    (different keys across iterations to avoid 7-day-TTL cross-iteration
    interference even though tmp_path isolation already ensures
    independence). Failures are localized to a specific iteration via the
    parametrize id ``[0..9]``.
    """
    db_path = tmp_path / f"state-{iteration}.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables(db_url)

    events_dir = tmp_path / f"events-{iteration}"
    app = build_app(base_dir=events_dir, db_url=db_url, clock=fixed_clock)

    # Per-iteration unique idempotency key: vary the RNG via the iteration
    # number so two iterations cannot collide in-process. (Each test instance
    # already has its own app + cache, so this is belt-and-braces.)
    from random import Random  # noqa: PLC0415 — local import keeps fixture leaner

    rng = Random(iteration * 1009 + 7)  # noqa: S311 — non-cryptographic test seed
    idempotency_key = new_idempotency_key(clock=fixed_clock, rng=rng)

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app),
            base_url="http://testserver",
        ) as client,
    ):
        await _run_100x_iteration(
            client,
            events_dir=events_dir,
            idempotency_key=idempotency_key,
            n=100,
            title=f"iter-{iteration}",
        )


# ---------------------------------------------------------------------------
# AC-8: post-completion sequential replay.
# ---------------------------------------------------------------------------


@pytest.mark.idempotency
@pytest.mark.asyncio
async def test_idempotency_post_completion_replay_returns_cached(
    app_client: tuple[AsyncClient, Path, Path],
    fixed_clock: FrozenClock,
) -> None:
    """Sequential same-key POSTs: second call returns cached body, NO new event."""
    client, _db_path, events_dir = app_client
    idempotency_key = new_idempotency_key(clock=fixed_clock)

    # First call — cache miss; factory runs.
    r1 = await client.post(
        "/v1/tasks",
        json={"title": "post-completion test"},
        headers={"Idempotency-Key": idempotency_key},
    )
    assert r1.status_code == 201
    assert r1.headers.get("X-Idempotency-Status") == "applied"

    # Second call — cache hit; body byte-identical.
    r2 = await client.post(
        "/v1/tasks",
        json={"title": "post-completion test"},
        headers={"Idempotency-Key": idempotency_key},
    )
    assert r2.status_code == 201
    assert r2.headers.get("X-Idempotency-Status") == "replayed"
    assert r2.content == r1.content
    # And the Location header still points to the same task_id from r1.
    assert r2.headers.get("Location") == r1.headers.get("Location")

    # A second sequential call did NOT emit a second task.created event.
    log_path = current_day_path(events_dir, FROZEN_EPOCH)
    envelopes = list(read_log_lines(log_path)) if log_path.exists() else []
    task_created_count = sum(1 for e in envelopes if e.type == "task.created")
    assert task_created_count == 1, (
        f"expected exactly 1 task.created event; got {task_created_count} — "
        "the cached replay path must not re-emit"
    )


# ---------------------------------------------------------------------------
# AC-9: error during first attempt does NOT cache.
# ---------------------------------------------------------------------------


@pytest.mark.idempotency
@pytest.mark.asyncio
async def test_idempotency_error_during_first_attempt_does_not_cache(
    tmp_path: Path,
    fixed_clock: FrozenClock,
) -> None:
    """Factory exception path: cache stays empty so a retry can succeed.

    Patch ``EventLogWriter.append`` to raise once then succeed. First POST
    surfaces a 500 (RFC 7807); second POST with the SAME key succeeds with
    201 + ``X-Idempotency-Status: applied`` (NOT ``replayed`` — the cache
    must NOT have stored the failed first attempt).
    """
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables(db_url)

    events_dir = tmp_path / "events"
    app = build_app(base_dir=events_dir, db_url=db_url, clock=fixed_clock)

    call_count: dict[str, int] = {"n": 0}
    real_append: Callable[[EventEnvelope], Awaitable[None]] | None = None

    async def _flaky_append(envelope: EventEnvelope) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("synthetic write failure")
        # Subsequent calls go through the real writer.
        assert real_append is not None
        await real_append(envelope)

    idempotency_key = new_idempotency_key(clock=fixed_clock)

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as client,
    ):
        # Capture the real append AFTER lifespan startup so writer is built.
        real_append = app.state.writer.append
        app.state.writer.append = _flaky_append

        # First POST — factory raises → 500 problem+json
        r1 = await client.post(
            "/v1/tasks",
            json={"title": "error path"},
            headers={"Idempotency-Key": idempotency_key},
        )
        assert r1.status_code == 500, f"expected 500 from synthetic failure; got {r1.status_code}"

        # Second POST — same key. Must NOT be served from cache; must invoke
        # factory again (call_count → 2) and return 201 + applied.
        r2 = await client.post(
            "/v1/tasks",
            json={"title": "error path"},
            headers={"Idempotency-Key": idempotency_key},
        )
        assert r2.status_code == 201, (
            f"expected 201 on retry; got {r2.status_code} — "
            "errors during first attempt MUST NOT be cached"
        )
        assert r2.headers.get("X-Idempotency-Status") == "applied", (
            "second call must show 'applied' (factory ran), NOT 'replayed' — "
            "cache must not contain the failed first attempt"
        )
        assert call_count["n"] == 2, (
            f"factory should have run twice (failed once, succeeded once); got {call_count['n']}"
        )
