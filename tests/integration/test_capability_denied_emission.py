"""Story 11.2.1 AC4 — end-to-end capability.denied counter increment.

Lives under ``tests/integration/`` (not inside any service) so it may
import from BOTH ``registry_api`` (producer) AND ``metrics_subscriber``
(consumer) without violating the cross-service import gate in
``scripts/check_imports.py`` (services may not import each other —
Architecture line 854).

Scope: trigger an HTTP-boundary tier denial → 403 returned, envelope
written to JSONL → read envelope back, drive it through
``metrics_subscriber.app.metrics.update_for`` → assert
``omb_capability_denied_total{tier=tier3, boundary=http}`` incremented
to 1.0. Closes the producer→consumer loop for Epic 10 retro DD5 at the
HTTP boundary. MCP boundary deferred to Story 11.2.2.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from capabilities import Tier
from events import FROZEN_EPOCH, FrozenClock
from events.canonical import from_canonical_json
from events.envelope import EventEnvelope
from events.payloads import CapabilityDeniedPayload
from httpx import ASGITransport, AsyncClient
from metrics_subscriber.app.metrics import build_collectors, update_for
from prometheus_client import CollectorRegistry
from registry_api.app import build_app

# PP8 (pass-1 review): SQLite test helpers now live in
# ``tests/integration/_db_helpers.py`` so future Story 11.2.2 / 12.x
# integration tests reuse them rather than re-vending the same 6 lines.
# Mirrors the existing ``_compose_helpers`` sibling-module pattern.
from tests.integration._db_helpers import integration_db_url, integration_seed_tables

_FROZEN_MONO_NS = 1_000_000


@pytest_asyncio.fixture(loop_scope="function")  # Story 8.7.6 PP2 — LifespanManager bg state
async def denied_app_client(
    tmp_path: Path,
) -> AsyncGenerator[tuple[AsyncClient, Path], None]:
    """Build an app where POST /v1/tasks requires Tier.THREE; worker actor will be denied.

    PP10 (pass-1 review): removed redundant ``REGISTRY_API_TEST_PROBES=1``
    monkeypatch — this fixture does NOT register the ``/debug/state``
    probe (no leaked test surface to gate), so the env var was dead.
    """
    db_path = tmp_path / "state.sqlite3"
    db_url = integration_db_url(db_path)
    await integration_seed_tables(db_url)
    events_dir = tmp_path / "events"
    clock = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)

    with patch(
        "registry_api.adapters.middleware.ROUTE_TIER_MAP",
        {"POST /v1/tasks": Tier.THREE},
    ):
        app = build_app(
            base_dir=events_dir,
            db_url=db_url,
            clock=clock,
            actor_kind="worker",
            idempotency_db_url=integration_db_url(tmp_path / "idempotency.sqlite3"),
            create_idempotency_schema_on_start=True,
        )
        async with (
            LifespanManager(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
        ):
            yield client, events_dir


@pytest.mark.asyncio
async def test_http_capability_denied_emits_envelope_and_increments_counter(
    denied_app_client: tuple[AsyncClient, Path],
) -> None:
    """AC1+AC3+AC4+AC5: HTTP 403 path emits envelope; counter increments end-to-end."""
    client, events_dir = denied_app_client

    r = await client.post("/v1/tasks", json={"title": "denied"})
    assert r.status_code == 403

    # Producer side: scan JSONL for the emitted envelope.
    envelopes: list[EventEnvelope] = []
    for log_file in sorted(events_dir.glob("*.jsonl")):
        for raw in log_file.read_bytes().splitlines():
            if not raw.strip():
                continue
            env = from_canonical_json(raw)
            if env.type == "capability.denied":
                envelopes.append(env)

    assert len(envelopes) == 1, (
        f"expected exactly 1 capability.denied envelope; got {len(envelopes)}"
    )
    env = envelopes[0]
    assert env.schema_version == "1.1.0"
    # PP6 (pass-1 review): ``from_canonical_json`` round-trips payload as
    # a frozen dict (not the registered Pydantic model — that path is
    # reserved for ``EventEnvelope.create``). Round-trip explicitly
    # through ``CapabilityDeniedPayload.model_validate`` so a future
    # field rename / type change fails this test instead of passing
    # silently via raw dict-indexing.
    payload = CapabilityDeniedPayload.model_validate(env.payload)
    assert payload.tier == "tier3"
    assert payload.boundary == "http"
    assert payload.attempted_action == "POST /v1/tasks"

    # Consumer side: dispatch through the metrics-subscriber materializer.
    registry = CollectorRegistry()
    state = build_collectors(registry=registry)
    update_for(state, env)

    value = registry.get_sample_value(
        "omb_capability_denied_total",
        {"tier": "tier3", "boundary": "http"},
    )
    assert value == 1.0, (
        f"omb_capability_denied_total{{tier=tier3, boundary=http}} expected 1.0, got {value}"
    )

    # And the family-level counter should also reflect the new event.
    family_value = registry.get_sample_value(
        "omb_events_appended_total",
        {"event_family": "capability"},
    )
    assert family_value == 1.0, (
        f"omb_events_appended_total{{event_family=capability}} expected 1.0, "
        f"got {family_value} (Story 11.2 P1-H2 family routing)"
    )
