"""Unit tests for events.envelope — EventEnvelope field validators + create() factory.

AC-6 / Story 2.1: ~15 tests.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ValidationError

from events.envelope import Actor, ActorKind, EventEnvelope
from events.errors import EventSchemaUnknown
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
