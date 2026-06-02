"""Unit tests for ``events.types.replication`` — Story 13.4 (NFR-R7).

Covers:

* ``ReplicationLaggingPayload`` round-trip via canonical-JSON encoder
* ``extra="forbid"`` rejects unknown fields
* Each field's constraint rejects malformed inputs (frozen/strict)
* Registration is live AND idempotent at import time, at BOTH 1.0.0 and 1.1.0
* ``replication.lagging`` resolves in the schema registry at 1.1.0 via
  ``EventEnvelope.create``

Test isolation: this module's payload registers as a side-effect of import.
Sibling autouse ``unregister_all()`` fixtures (test_canonical.py /
test_envelope.py) blast the registry, so an autouse fixture re-registers the
canonical class before each test — mirrors test_deployment.py.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any, cast

import pytest
from pydantic import BaseModel, ValidationError

import events.schema_registry as sr
from events.canonical import from_canonical_json, to_canonical_json
from events.clock import FROZEN_EPOCH, FrozenClock
from events.envelope import Actor, EventEnvelope
from events.ids import new_event_id, new_request_id
from events.schema_registry import REGISTRY, register
from events.types.replication import ReplicationLaggingPayload

_VALID_KWARGS: dict[str, Any] = {
    "db": "/var/lib/oh-my-bmad/registry/state.sqlite3",
    "signal": "sync_stalled",
    "threshold_seconds": 30,
    "sustained_seconds": 360,
    "sync_error_count": 7,
}


@pytest.fixture(autouse=True)
def _ensure_replication_registered() -> Generator[None, None, None]:
    """Compensate for sibling-file ``unregister_all()`` cascades.

    Re-register both versions before each test so the registry is in a known
    state regardless of suite ordering. ``register()`` is idempotent for
    same-model re-register (schema_registry.py:80).
    """
    register("replication.lagging", "1.0.0", ReplicationLaggingPayload)
    register("replication.lagging", "1.1.0", ReplicationLaggingPayload)
    yield


@pytest.fixture()
def _isolated_registry() -> Generator[None, None, None]:
    """Snapshot REGISTRY around tests that mutate it."""
    snapshot = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)
    sr._rebuild_types_cache()  # noqa: SLF001 — test-only registry rebuild


class TestRegistration:
    def test_registered_at_module_load_both_versions(self) -> None:
        assert ("replication.lagging", "1.0.0") in REGISTRY
        assert ("replication.lagging", "1.1.0") in REGISTRY
        assert REGISTRY[("replication.lagging", "1.0.0")] is ReplicationLaggingPayload
        assert REGISTRY[("replication.lagging", "1.1.0")] is ReplicationLaggingPayload
        assert "replication.lagging" in sr.EVENT_TYPES

    def test_register_is_idempotent_on_reimport(self) -> None:
        register("replication.lagging", "1.1.0", ReplicationLaggingPayload)
        assert REGISTRY[("replication.lagging", "1.1.0")] is ReplicationLaggingPayload

    def test_register_different_model_at_same_key_rejected(self, _isolated_registry: None) -> None:
        class _Imposter(BaseModel):
            x: int

        with pytest.raises(ValueError, match="already registered"):
            register("replication.lagging", "1.1.0", _Imposter)


class TestSchemaResolution:
    def test_resolves_at_1_1_0_via_envelope_create(self) -> None:
        """``EventEnvelope.create`` resolves replication.lagging at 1.1.0."""
        clock = FrozenClock(1_000_000_000, now=FROZEN_EPOCH)
        payload = ReplicationLaggingPayload(**_VALID_KWARGS)
        env = EventEnvelope.create(
            event_id=new_event_id(),
            schema_version="1.1.0",
            type="replication.lagging",
            emitted_at=clock.now(),
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor=Actor(kind="system", id="litestream-lag-check"),
            payload=payload,
            trace_id="01917e5c-a7d1-7000-8abc-000000000000",
            request_id=new_request_id(),
        )
        assert env.type == "replication.lagging"
        assert env.schema_version == "1.1.0"

    def test_resolves_at_1_0_0_via_envelope_create(self) -> None:
        clock = FrozenClock(1_000_000_000, now=FROZEN_EPOCH)
        payload = ReplicationLaggingPayload(**_VALID_KWARGS)
        env = EventEnvelope.create(
            event_id=new_event_id(),
            schema_version="1.0.0",
            type="replication.lagging",
            emitted_at=clock.now(),
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor=Actor(kind="system", id="litestream-lag-check"),
            payload=payload,
            trace_id="01917e5c-a7d1-7000-8abc-000000000000",
            request_id=new_request_id(),
        )
        assert env.schema_version == "1.0.0"


class TestPayloadRoundTrip:
    def test_envelope_round_trips_through_canonical_json(self) -> None:
        clock = FrozenClock(1_000_000_000, now=FROZEN_EPOCH)
        payload = ReplicationLaggingPayload(**_VALID_KWARGS)
        env = EventEnvelope.create(
            event_id=new_event_id(),
            schema_version="1.1.0",
            type="replication.lagging",
            emitted_at=clock.now(),
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor=Actor(kind="system", id="litestream-lag-check"),
            payload=payload,
            trace_id="01917e5c-a7d1-7000-8abc-000000000000",
            request_id=new_request_id(),
        )
        line = to_canonical_json(env)
        restored = from_canonical_json(line)
        assert restored.type == "replication.lagging"
        assert isinstance(restored.payload, dict)
        assert restored.payload["db"] == _VALID_KWARGS["db"]
        assert restored.payload["signal"] == "sync_stalled"
        assert restored.payload["threshold_seconds"] == 30
        assert restored.payload["sustained_seconds"] == 360
        assert restored.payload["sync_error_count"] == 7


class TestExtraForbid:
    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ReplicationLaggingPayload(
                **_VALID_KWARGS,
                unexpected="value",  # type: ignore[call-arg]  # extra="forbid" rejects at runtime
            )
        assert "extra" in str(exc_info.value).lower()


class TestDbField:
    def test_empty_db_rejected(self) -> None:
        kwargs = {**_VALID_KWARGS, "db": ""}
        with pytest.raises(ValidationError):
            ReplicationLaggingPayload(**kwargs)

    def test_db_at_max_length_accepted(self) -> None:
        kwargs = {**_VALID_KWARGS, "db": "/" + "a" * 4095}
        ReplicationLaggingPayload(**kwargs)

    def test_db_over_max_length_rejected(self) -> None:
        kwargs = {**_VALID_KWARGS, "db": "/" + "a" * 4096}
        with pytest.raises(ValidationError):
            ReplicationLaggingPayload(**kwargs)


class TestSignalLiteral:
    @pytest.mark.parametrize(
        "bad_signal", ["stalled", "sync_stall", "error_spike", "", "SYNC_STALLED"]
    )
    def test_unknown_signal_rejected(self, bad_signal: str) -> None:
        kwargs = {**_VALID_KWARGS, "signal": bad_signal}
        with pytest.raises(ValidationError):
            ReplicationLaggingPayload(**kwargs)

    @pytest.mark.parametrize("good_signal", ["sync_stalled", "silent_stall"])
    def test_known_signal_accepted(self, good_signal: str) -> None:
        # Story 13.4a added "silent_stall" — both Literal values must validate.
        ReplicationLaggingPayload(**{**_VALID_KWARGS, "signal": good_signal})


class TestThresholdSeconds:
    @pytest.mark.parametrize("bad", [0, -1, -30])
    def test_non_positive_rejected(self, bad: int) -> None:
        kwargs = {**_VALID_KWARGS, "threshold_seconds": bad}
        with pytest.raises(ValidationError):
            ReplicationLaggingPayload(**kwargs)

    def test_positive_accepted(self) -> None:
        ReplicationLaggingPayload(**{**_VALID_KWARGS, "threshold_seconds": 1})


class TestSustainedSeconds:
    def test_negative_rejected(self) -> None:
        kwargs = {**_VALID_KWARGS, "sustained_seconds": -1}
        with pytest.raises(ValidationError):
            ReplicationLaggingPayload(**kwargs)

    def test_zero_accepted(self) -> None:
        ReplicationLaggingPayload(**{**_VALID_KWARGS, "sustained_seconds": 0})


class TestSyncErrorCount:
    def test_negative_rejected(self) -> None:
        kwargs = {**_VALID_KWARGS, "sync_error_count": -1}
        with pytest.raises(ValidationError):
            ReplicationLaggingPayload(**kwargs)

    def test_zero_accepted(self) -> None:
        ReplicationLaggingPayload(**{**_VALID_KWARGS, "sync_error_count": 0})


class TestFrozenStrict:
    def test_post_construction_mutation_rejected(self) -> None:
        payload = ReplicationLaggingPayload(**_VALID_KWARGS)
        with pytest.raises(ValidationError):
            payload.db = "/var/lib/other.sqlite3"

    def test_string_coercion_rejected_under_strict(self) -> None:
        # ``strict=True`` rejects coercion: int-as-string fails.
        kwargs = cast(dict[str, object], {**_VALID_KWARGS, "threshold_seconds": "30"})
        with pytest.raises(ValidationError):
            ReplicationLaggingPayload(**kwargs)  # type: ignore[arg-type]
