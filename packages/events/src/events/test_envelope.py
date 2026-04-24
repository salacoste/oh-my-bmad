"""Unit tests for events.envelope — EventEnvelope field validators + create() factory.

AC-6 / Story 2.1: ~15 tests.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from events.envelope import Actor, ActorKind, EventEnvelope
from events.errors import EventSchemaUnknown, EventValidationError
from events.schema_registry import register, unregister_all

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_VALID_EVENT_ID = "e-01917e5c-a7d1-7000-8000-000000000001"
_VALID_REQUEST_ID = "01917e5c-a7d1-7000-8000-000000000002"
_VALID_EMITTED_AT = datetime(2026, 4, 21, 10, 30, 0, tzinfo=UTC)


class _TaskPayload(BaseModel):
    task_id: str


def _make_envelope(**overrides: object) -> EventEnvelope:
    """Return a valid EventEnvelope using sensible defaults."""
    kwargs: dict[str, object] = dict(
        event_id=_VALID_EVENT_ID,
        schema_version="1.0.0",
        type="task.created",
        emitted_at=_VALID_EMITTED_AT,
        emitted_at_monotonic_ns=1_000_000,
        actor=Actor(kind="system", id="test-system"),
        payload={"task_id": "abc"},
        request_id=_VALID_REQUEST_ID,
    )
    kwargs.update(overrides)
    return EventEnvelope(**kwargs)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clean_registry() -> Generator[None, None, None]:
    unregister_all()
    register("task.created", "1.0.0", _TaskPayload)
    yield
    unregister_all()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestValidEnvelopeConstruction:
    def test_valid_envelope_builds(self) -> None:
        env = _make_envelope()
        assert env.event_id == _VALID_EVENT_ID
        assert env.type == "task.created"
        assert env.schema_version == "1.0.0"


class TestFrozenMutation:
    def test_mutation_raises(self) -> None:
        env = _make_envelope()
        with pytest.raises((ValidationError, TypeError)):
            env.payload = {}


class TestEventIdShape:
    def test_valid_uuidv7_with_prefix(self) -> None:
        env = _make_envelope(event_id="e-01917e5c-a7d1-7000-8abc-000000000001")
        assert env.event_id.startswith("e-")

    def test_missing_e_prefix_rejected(self) -> None:
        with pytest.raises(ValidationError, match="event_id"):
            _make_envelope(event_id="01917e5c-a7d1-7000-8000-000000000001")

    def test_wrong_version_nibble_rejected(self) -> None:
        # version nibble must be 7; use 4 here
        with pytest.raises(ValidationError, match="event_id"):
            _make_envelope(event_id="e-01917e5c-a7d1-4000-8000-000000000001")

    def test_valid_parent_event_id(self) -> None:
        env = _make_envelope(parent_event_id="e-01917e5c-a7d1-7000-8000-000000000099")
        assert env.parent_event_id is not None

    def test_invalid_parent_event_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="parent_event_id"):
            _make_envelope(parent_event_id="bad-id")

    def test_none_parent_event_id_accepted(self) -> None:
        env = _make_envelope(parent_event_id=None)
        assert env.parent_event_id is None


class TestRequestIdShape:
    def test_bare_uuidv7_accepted(self) -> None:
        env = _make_envelope(request_id="01917e5c-a7d1-7000-8000-000000000099")
        assert env.request_id == "01917e5c-a7d1-7000-8000-000000000099"

    def test_prefixed_request_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="request_id"):
            _make_envelope(request_id="e-01917e5c-a7d1-7000-8000-000000000099")


class TestNaiveDatetimeRejected:
    def test_naive_datetime_raises(self) -> None:
        with pytest.raises(ValidationError):
            _make_envelope(emitted_at=datetime(2026, 4, 21, 10, 30, 0))


class TestNonUtcDatetimeRejected:
    def test_non_utc_offset_raises(self) -> None:
        non_utc = datetime(2026, 4, 21, 10, 30, 0, tzinfo=timezone(timedelta(hours=5)))
        with pytest.raises(ValidationError, match="UTC"):
            _make_envelope(emitted_at=non_utc)


class TestUtcDatetimeAccepted:
    def test_utc_datetime_accepted(self) -> None:
        env = _make_envelope(emitted_at=datetime(2026, 4, 21, 0, 0, 0, tzinfo=UTC))
        assert env.emitted_at.tzinfo is not None


class TestSchemaVersionRegex:
    def test_valid_semver(self) -> None:
        env = _make_envelope(schema_version="2.10.3")
        assert env.schema_version == "2.10.3"

    def test_missing_patch_rejected(self) -> None:
        with pytest.raises(ValidationError, match="schema_version"):
            _make_envelope(schema_version="1.0")

    def test_non_numeric_rejected(self) -> None:
        with pytest.raises(ValidationError, match="schema_version"):
            _make_envelope(schema_version="v1.0.0")


class TestTypeRegex:
    def test_valid_dotted_type(self) -> None:
        register("task.updated", "1.0.0", _TaskPayload)
        env = _make_envelope(type="task.updated")
        assert env.type == "task.updated"

    def test_single_segment_rejected(self) -> None:
        with pytest.raises(ValidationError, match="type"):
            _make_envelope(type="taskcreated")

    def test_uppercase_rejected(self) -> None:
        with pytest.raises(ValidationError, match="type"):
            _make_envelope(type="Task.Created")


class TestActorKindLiteral:
    def test_valid_kinds(self) -> None:
        valid_kinds: list[ActorKind] = ["operator", "orchestrator", "worker", "system", "clawhip"]
        for kind in valid_kinds:
            actor = Actor(kind=kind, id="x")
            assert actor.kind == kind

    def test_invalid_kind_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Actor(kind="admin", id="x")  # type: ignore[arg-type]


class TestExtraFieldsForbidden:
    def test_extra_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            EventEnvelope(  # type: ignore[call-arg]
                event_id=_VALID_EVENT_ID,
                schema_version="1.0.0",
                type="task.created",
                emitted_at=_VALID_EMITTED_AT,
                emitted_at_monotonic_ns=0,
                actor=Actor(kind="system", id="sys"),
                payload={"task_id": "x"},
                request_id=_VALID_REQUEST_ID,
                unknown_extra_field="oops",
            )


class TestRoundTripViaJson:
    def test_model_validate_json_roundtrip(self) -> None:
        env = _make_envelope()
        from events.canonical import to_canonical_json

        data = to_canonical_json(env)
        env2 = EventEnvelope.model_validate_json(data)
        assert env2.event_id == env.event_id
        assert env2.type == env.type
        assert env2.schema_version == env.schema_version


class TestCreateFactoryUnregistered:
    def test_raises_event_schema_unknown(self) -> None:
        with pytest.raises(EventSchemaUnknown) as exc_info:
            EventEnvelope.create(
                event_id=_VALID_EVENT_ID,
                schema_version="1.0.0",
                type="unknown.event",
                emitted_at=_VALID_EMITTED_AT,
                emitted_at_monotonic_ns=0,
                actor=Actor(kind="system", id="sys"),
                payload={},
                request_id=_VALID_REQUEST_ID,
            )
        assert exc_info.value.event_type == "unknown.event"


class TestCreateFactoryRegistered:
    def test_create_validates_payload_against_model(self) -> None:
        env = EventEnvelope.create(
            event_id=_VALID_EVENT_ID,
            schema_version="1.0.0",
            type="task.created",
            emitted_at=_VALID_EMITTED_AT,
            emitted_at_monotonic_ns=0,
            actor=Actor(kind="system", id="sys"),
            payload={"task_id": "my-task"},
            request_id=_VALID_REQUEST_ID,
        )
        assert isinstance(env.payload, _TaskPayload)
        assert env.payload.task_id == "my-task"


# ---------------------------------------------------------------------------
# Regression tests — code-review follow-up for Story 2.1
# ---------------------------------------------------------------------------


class TestEventSchemaUnknownReportsLiveTypes:
    """Fix A — EVENT_TYPES was stale-bound: create() always reported empty."""

    def test_event_schema_unknown_reports_live_registered_types(self) -> None:
        """EventEnvelope.create() must report CURRENT registered types in the error."""
        from events import schema_registry as sr

        sr.unregister_all()
        sr.register("known.type", "1.0.0", _TaskPayload)
        with pytest.raises(EventSchemaUnknown) as exc_info:
            EventEnvelope.create(
                event_id=_VALID_EVENT_ID,
                schema_version="1.0.0",
                type="unknown.type",
                emitted_at=_VALID_EMITTED_AT,
                emitted_at_monotonic_ns=1,
                actor=Actor(kind="system", id="sys"),
                payload={},
                request_id=_VALID_REQUEST_ID,
            )
        assert "known.type" in exc_info.value.registered_types
        sr.unregister_all()


class TestPayloadImmutable:
    """Fix C — Pydantic frozen=True blocks attribute rebind but not nested mutation."""

    def test_payload_dict_setitem_rejected(self) -> None:
        env = _make_envelope(payload={"k": "v"})
        payload = cast(dict[str, Any], env.payload)
        with pytest.raises(TypeError):
            payload["k"] = "changed"

    def test_payload_dict_new_key_rejected(self) -> None:
        env = _make_envelope(payload={"k": "v"})
        payload = cast(dict[str, Any], env.payload)
        with pytest.raises(TypeError):
            payload["new"] = "injected"

    def test_payload_dict_delete_rejected(self) -> None:
        env = _make_envelope(payload={"k": "v"})
        payload = cast(dict[str, Any], env.payload)
        with pytest.raises(TypeError):
            del payload["k"]

    def test_payload_dict_update_rejected(self) -> None:
        env = _make_envelope(payload={"k": "v"})
        payload = cast(dict[str, Any], env.payload)
        with pytest.raises(TypeError):
            payload.update({"k": "x"})

    def test_payload_dict_clear_rejected(self) -> None:
        env = _make_envelope(payload={"k": "v"})
        payload = cast(dict[str, Any], env.payload)
        with pytest.raises(TypeError):
            payload.clear()

    def test_payload_nested_dict_also_frozen(self) -> None:
        env = _make_envelope(payload={"outer": {"inner": 1}})
        payload = cast(dict[str, Any], env.payload)
        nested = cast(dict[str, Any], payload["outer"])
        with pytest.raises(TypeError):
            nested["inner"] = 99


class TestEmittedAtNormalization:
    """Fix B / Fix E — datetime is canonicalized at parse time."""

    def test_plus_00_00_input_normalized_to_utc(self) -> None:
        plus_tz = timezone(timedelta(0))  # not UTC identity, but zero offset
        env = _make_envelope(emitted_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=plus_tz))
        assert env.emitted_at.tzinfo is UTC

    def test_gmt_tzinfo_normalized_to_utc(self) -> None:
        """Any zero-offset tzinfo is normalized to UTC."""
        gmt_tz = timezone(timedelta(0), "GMT")
        env = _make_envelope(emitted_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=gmt_tz))
        assert env.emitted_at.tzinfo is UTC

    def test_microsecond_precision_truncated_to_millisecond(self) -> None:
        dt = datetime(2026, 4, 21, 10, 30, 0, 123456, tzinfo=UTC)
        env = _make_envelope(emitted_at=dt)
        # 123456 microseconds truncated to 123000 (= 123 ms).
        assert env.emitted_at.microsecond == 123000


class _OtherPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    unrelated_field: str


class _ExpectedPayload(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id: str


class TestCreateWrongBaseModelPayload:
    """Fix F — create() must validate BaseModel payloads against the registered model."""

    def test_wrong_basemodel_subclass_coerced_via_roundtrip(self) -> None:
        from events import schema_registry as sr

        sr.unregister_all()
        sr.register("typed.event", "1.0.0", _ExpectedPayload)
        other = _OtherPayload(unrelated_field="x")
        # Should either succeed via coercion or raise ValidationError — NOT silently accept.
        with pytest.raises((ValidationError, EventValidationError)):
            EventEnvelope.create(
                event_id=_VALID_EVENT_ID,
                schema_version="1.0.0",
                type="typed.event",
                emitted_at=_VALID_EMITTED_AT,
                emitted_at_monotonic_ns=0,
                actor=Actor(kind="system", id="sys"),
                payload=other,
                request_id=_VALID_REQUEST_ID,
            )
        sr.unregister_all()


# ---------------------------------------------------------------------------
# Story 2.2 integration tests — generator output accepted by Story 2.1 validators
# ---------------------------------------------------------------------------


class TestGeneratorIntegration:
    """AC-7 / Story 2.2: generators produce IDs that Story 2.1 validators accept."""

    def test_envelope_accepts_generator_output(self) -> None:
        """Story 2.2 generators produce IDs Story 2.1 validators accept."""
        from random import Random

        from events import FrozenClock, new_event_id, new_request_id

        clock = FrozenClock()
        rng = Random(42)
        env = EventEnvelope(
            event_id=new_event_id(clock=clock, rng=rng),
            schema_version="1.0.0",
            type="task.created",
            emitted_at=clock.now(),
            emitted_at_monotonic_ns=clock.monotonic_ns(),
            actor=Actor(kind="system", id="x"),
            payload={"task_id": "abc"},
            request_id=new_request_id(clock=clock, rng=rng),
        )
        assert env.event_id.startswith("e-")

    def test_deterministic_generator_produces_valid_envelope(self) -> None:
        """Seeded clock+rng give a reproducible, envelope-valid event_id."""
        from random import Random

        from events import FrozenClock, new_event_id

        clock1 = FrozenClock()
        rng1 = Random(99)
        clock2 = FrozenClock()
        rng2 = Random(99)
        eid1 = new_event_id(clock=clock1, rng=rng1)
        eid2 = new_event_id(clock=clock2, rng=rng2)
        assert eid1 == eid2
        # Both should construct without ValidationError
        env = EventEnvelope(
            event_id=eid1,
            schema_version="1.0.0",
            type="task.created",
            emitted_at=_VALID_EMITTED_AT,
            emitted_at_monotonic_ns=0,
            actor=Actor(kind="system", id="gen-test"),
            payload={"task_id": "gen-task"},
            request_id=_VALID_REQUEST_ID,
        )
        assert env.event_id == eid2

    def test_generator_event_id_passes_envelope_regex(self) -> None:
        """new_event_id() output satisfies the _EVENT_ID_RE from envelope.py."""
        from random import Random

        from events import FrozenClock, new_event_id
        from events.envelope import _EVENT_ID_RE

        for seed in range(5):
            eid = new_event_id(clock=FrozenClock(), rng=Random(seed))
            assert _EVENT_ID_RE.match(eid), f"Seed {seed}: {eid!r} failed regex"
