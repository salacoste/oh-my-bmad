"""Unit tests for events.errors — typed exception hierarchy.

AC-6 / Story 2.1: ~6 tests covering hierarchy, attrs, str() formatting.
"""

from __future__ import annotations

import pytest

from events.errors import (
    CanonicalSerializationError,
    EventSchemaUnknown,
    EventsError,
    EventValidationError,
)


class TestEventsErrorHierarchy:
    def test_events_error_is_exception(self) -> None:
        assert issubclass(EventsError, Exception)

    def test_event_schema_unknown_is_events_error(self) -> None:
        assert issubclass(EventSchemaUnknown, EventsError)

    def test_event_validation_error_is_events_error(self) -> None:
        assert issubclass(EventValidationError, EventsError)

    def test_canonical_serialization_error_is_events_error(self) -> None:
        assert issubclass(CanonicalSerializationError, EventsError)


class TestEventSchemaUnknown:
    def test_attrs_round_trip(self) -> None:
        exc = EventSchemaUnknown(
            "task.created", "1.0.0", frozenset({"task.updated", "task.deleted"})
        )
        assert exc.event_type == "task.created"
        assert exc.schema_version == "1.0.0"
        assert exc.registered_types == frozenset({"task.updated", "task.deleted"})

    def test_str_includes_all_three(self) -> None:
        exc = EventSchemaUnknown("task.created", "2.0.0", frozenset({"task.updated"}))
        msg = str(exc)
        assert "task.created" in msg
        assert "2.0.0" in msg
        assert "task.updated" in msg

    def test_is_catchable_as_events_error(self) -> None:
        with pytest.raises(EventsError):
            raise EventSchemaUnknown("x.y", "1.0.0", frozenset())


class TestEventSchemaUnknownEmptyRegistry:
    def test_formats_empty_registry_sentinel(self) -> None:
        exc = EventSchemaUnknown("x.y", "1.0.0", frozenset())
        assert "(empty registry)" in str(exc)


class TestCanonicalSerializationError:
    def test_raised_and_catchable_as_events_error(self) -> None:
        with pytest.raises(EventsError):
            raise CanonicalSerializationError("NaN not allowed")

    def test_message_preserved(self) -> None:
        exc = CanonicalSerializationError("NaN not allowed")
        assert str(exc) == "NaN not allowed"


class TestEventValidationError:
    def test_pydantic_error_attr_preserved(self) -> None:
        original = ValueError("pydantic noise")
        exc = EventValidationError("clean message", pydantic_error=original)
        assert exc.pydantic_error is original
        assert str(exc) == "clean message"

    def test_pydantic_error_defaults_none(self) -> None:
        exc = EventValidationError("msg")
        assert exc.pydantic_error is None
