"""Route-level tests for GET /v1/key-status (Story 11.5.1 / AC1 + AC6).

Two tests:

1. ``test_get_key_status_returns_seeded_row`` — seed a KeyFingerprint row,
   GET /v1/key-status, assert 200 + typed response shape (fingerprint,
   rotated_at ISO-Z, rotated_by_actor_id).
2. ``test_get_key_status_returns_404_when_row_missing`` — cold-start path:
   no KeyFingerprint row, GET /v1/key-status, assert 404 + RFC 7807 problem
   detail mentions the cold-start guidance.

Pattern mirrors ``services/registry-api/src/registry_api/test_app.py`` —
ASGITransport + LifespanManager so the full lifespan (writer + engine +
session_maker) is exercised. No mocks for the data path; only the underlying
SQLite is seeded.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from httpx import ASGITransport, AsyncClient
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — services→services allowed per AC-16
    create_engine as _create_engine,
)
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — services→services allowed per AC-16
    get_session,
)
from registry_state.schema import (  # noqa: IMP001 — services→services allowed per AC-16
    Base,
    KeyFingerprint,
)

from registry_api.app import build_app

_FROZEN_MONO_NS = 1_000_000

# Story 11.5 AC1 fixture values — 16-hex per ADR-0006 §Key-fingerprint.
_SEEDED_FINGERPRINT = "a1b2c3d4e5f6789a"
_SEEDED_ROTATED_BY = "http-api"


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _seed_tables(db_url: str) -> None:
    """Create all ORM tables in the writable DB at *db_url*."""
    engine = _create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _seed_key_fingerprint(db_url: str, rotated_at: datetime) -> None:
    """Insert a deterministic singleton ``KeyFingerprint`` row."""
    engine = _create_engine(db_url)
    session_maker = get_session(engine)
    async with session_maker() as session:
        row = KeyFingerprint(
            id="current",
            fingerprint=_SEEDED_FINGERPRINT,
            rotated_at=rotated_at,
            rotated_by_actor_id=_SEEDED_ROTATED_BY,
        )
        session.add(row)  # noqa: SW001 — test-only fixture seeding, not production write path
        await session.commit()
    await engine.dispose()


@pytest.fixture
def fixed_clock() -> FrozenClock:
    """Stationary clock at FROZEN_EPOCH with mono_ns=1_000_000."""
    return FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)


@pytest_asyncio.fixture(loop_scope="function")
async def client_with_seeded_row(
    tmp_path: Path, fixed_clock: FrozenClock
) -> AsyncGenerator[AsyncClient, None]:
    """Client with a pre-seeded KeyFingerprint row (AC1 happy path)."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables(db_url)
    await _seed_key_fingerprint(
        db_url,
        # Use a deterministic non-epoch timestamp so the test asserts on a
        # value that's distinguishable from FROZEN_EPOCH defaults elsewhere.
        rotated_at=datetime(2026, 5, 21, 14, 30, 0, tzinfo=UTC),
    )

    events_dir = tmp_path / "events"
    app = build_app(base_dir=events_dir, db_url=db_url, clock=fixed_clock)

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client


@pytest_asyncio.fixture(loop_scope="function")
async def client_without_seeded_row(
    tmp_path: Path, fixed_clock: FrozenClock
) -> AsyncGenerator[AsyncClient, None]:
    """Client with empty KeyFingerprint table (AC1 cold-start path)."""
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables(db_url)  # tables exist, but no row inserted

    events_dir = tmp_path / "events"
    app = build_app(base_dir=events_dir, db_url=db_url, clock=fixed_clock)

    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://testserver"
        ) as client,
    ):
        yield client


# ---------------------------------------------------------------------------
# AC1 happy-path — seeded row → 200 + typed shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_key_status_returns_seeded_row(client_with_seeded_row: AsyncClient) -> None:
    """AC1 happy path: KeyFingerprint row present → 200 with full triple."""
    response = await client_with_seeded_row.get("/v1/key-status")

    assert response.status_code == 200, (
        f"expected 200, got {response.status_code}; body={response.text!r}"
    )

    body = response.json()
    # AC1 shape contract — matches KeyStatusResponse pydantic model.
    assert body["fingerprint"] == _SEEDED_FINGERPRINT
    assert body["rotated_by_actor_id"] == _SEEDED_ROTATED_BY
    # ISO-Z serialization (pydantic default for datetime); the seeded UTC
    # datetime round-trips as "2026-05-21T14:30:00Z" or with explicit offset.
    assert "2026-05-21" in body["rotated_at"]
    assert "14:30:00" in body["rotated_at"]


# ---------------------------------------------------------------------------
# AC1 cold-start path — no row → 404 problem+json
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_key_status_returns_404_when_row_missing(
    client_without_seeded_row: AsyncClient,
) -> None:
    """AC1 cold-start: empty KeyFingerprint table → 404 with operator-readable detail."""
    response = await client_without_seeded_row.get("/v1/key-status")

    assert response.status_code == 404, (
        f"expected 404, got {response.status_code}; body={response.text!r}"
    )

    # Story 11.5.1 /code-review-fix M3: pin the RFC 7807 Content-Type so a
    # future regression that swaps the registry-api ``handle_http_exception``
    # adapter for FastAPI's default ``{detail: ...}`` envelope is caught here.
    content_type = response.headers.get("content-type", "")
    assert "application/problem+json" in content_type, (
        f"expected RFC 7807 problem+json content-type; got {content_type!r}"
    )

    body = response.json()
    # RFC 7807 problem+json shape per the handle_http_exception adapter.
    # detail field should mention the cold-start guidance string from the route.
    detail = body.get("detail", "")
    assert "key fingerprint not yet materialized" in detail or "not yet materialized" in detail, (
        f"detail should explain cold-start; got {detail!r}"
    )
