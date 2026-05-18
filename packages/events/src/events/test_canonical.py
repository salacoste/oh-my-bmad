"""Unit tests for events.canonical — deterministic byte-identical serialization.

AC-6 / Story 2.1: ~10 tests.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from events.canonical import (
    from_canonical_json,
    to_canonical_json,
    to_canonical_payload_json,
)
from events.envelope import Actor, EventEnvelope
from events.errors import CanonicalSerializationError
from events.schema_registry import register, unregister_all

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_EVENT_ID = "e-01917e5c-a7d1-7000-8000-000000000001"
_VALID_REQUEST_ID = "01917e5c-a7d1-7000-8000-000000000002"
_VALID_EMITTED_AT = datetime(2026, 4, 21, 10, 30, 0, 123000, tzinfo=UTC)
_VALID_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000000"


class _SimplePayload(BaseModel):
    value: str


@pytest.fixture(autouse=True)
def _clean_registry() -> Generator[None, None, None]:
    unregister_all()
    register("task.created", "1.0.0", _SimplePayload)
    register("task.created", "1.1.0", _SimplePayload)
    yield
    unregister_all()


def _make_envelope(**overrides: object) -> EventEnvelope:
    kwargs: dict[str, object] = dict(
        event_id=_VALID_EVENT_ID,
        schema_version="1.0.0",
        type="task.created",
        emitted_at=_VALID_EMITTED_AT,
        emitted_at_monotonic_ns=999,
        actor=Actor(kind="system", id="sys"),
        payload={"value": "hello"},
        trace_id=_VALID_TRACE_ID,
        request_id=_VALID_REQUEST_ID,
    )
    kwargs.update(overrides)
    return EventEnvelope(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestToCanonicalJsonReturnsBytes:
    def test_returns_bytes(self) -> None:
        env = _make_envelope()
        result = to_canonical_json(env)
        assert isinstance(result, bytes)


class TestByteIdenticalOutput:
    def test_same_envelope_two_invocations_identical(self) -> None:
        env = _make_envelope()
        assert to_canonical_json(env) == to_canonical_json(env)


class TestUtcZSuffix:
    def test_emitted_at_has_z_suffix_not_plus_00(self) -> None:
        env = _make_envelope()
        data = to_canonical_json(env)
        text = data.decode("utf-8")
        assert "+00:00" not in text
        assert "Z" in text

    def test_z_suffix_format_matches_spec(self) -> None:
        # Architecture line 360: 2026-04-21T10:30:00.123Z
        env = _make_envelope(emitted_at=datetime(2026, 4, 21, 10, 30, 0, 123000, tzinfo=UTC))
        text = to_canonical_json(env).decode("utf-8")
        assert "2026-04-21T10:30:00.123Z" in text


class TestMillisecondPrecision:
    def test_sub_ms_microseconds_stripped(self) -> None:
        # 123456 microseconds → 123000 after truncation → .123Z
        env = _make_envelope(emitted_at=datetime(2026, 4, 21, 10, 30, 0, 123456, tzinfo=UTC))
        text = to_canonical_json(env).decode("utf-8")
        # Should be .123Z not .123456
        assert "123456" not in text
        assert "2026-04-21T10:30:00.123Z" in text


class TestSortKeysDeterminism:
    def test_dict_payload_key_order_irrelevant(self) -> None:
        """Dict payload with different insertion orders serializes byte-identically (sort_keys)."""
        # Insertion order differs; json.dumps(sort_keys=True) normalizes.
        env_ba = _make_envelope(payload={"b": "hello", "a": 1})
        env_ab = _make_envelope(payload={"a": 1, "b": "hello"})
        assert to_canonical_json(env_ba) == to_canonical_json(env_ab)

    def test_output_keys_are_sorted(self) -> None:
        env = _make_envelope()
        text = to_canonical_json(env).decode("utf-8")
        # actor comes before emitted_at alphabetically
        assert text.index('"actor"') < text.index('"emitted_at"')


class TestAllowNanFalse:
    def test_nan_payload_raises_canonical_error(self) -> None:
        class _NanPayload(BaseModel):
            val: float

        register("task.nan", "1.0.0", _NanPayload)
        env = EventEnvelope(
            event_id=_VALID_EVENT_ID,
            schema_version="1.0.0",
            type="task.nan",
            emitted_at=_VALID_EMITTED_AT,
            emitted_at_monotonic_ns=0,
            actor=Actor(kind="system", id="sys"),
            payload={"val": float("nan")},
            trace_id=_VALID_TRACE_ID,
            request_id=_VALID_REQUEST_ID,
        )
        with pytest.raises(CanonicalSerializationError):
            to_canonical_json(env)

    def test_inf_payload_raises_canonical_error(self) -> None:
        class _InfPayload(BaseModel):
            val: float

        register("task.inf", "1.0.0", _InfPayload)
        env = EventEnvelope(
            event_id=_VALID_EVENT_ID,
            schema_version="1.0.0",
            type="task.inf",
            emitted_at=_VALID_EMITTED_AT,
            emitted_at_monotonic_ns=0,
            actor=Actor(kind="system", id="sys"),
            payload={"val": float("inf")},
            trace_id=_VALID_TRACE_ID,
            request_id=_VALID_REQUEST_ID,
        )
        with pytest.raises(CanonicalSerializationError):
            to_canonical_json(env)


class TestUtf8Encoding:
    def test_output_is_valid_utf8(self) -> None:
        env = _make_envelope()
        data = to_canonical_json(env)
        # Should decode without error
        text = data.decode("utf-8")
        assert len(text) > 0


class TestRoundTrip:
    def test_from_canonical_json_round_trips(self) -> None:
        env = _make_envelope()
        data = to_canonical_json(env)
        env2 = from_canonical_json(data)
        assert env2.event_id == env.event_id
        assert env2.type == env.type
        assert env2.schema_version == env.schema_version
        assert env2.request_id == env.request_id
        # emitted_at: truncated to ms in serialization, so microsecond may differ
        assert env2.emitted_at.year == env.emitted_at.year

    def test_round_trip_is_byte_stable(self) -> None:
        """Serializing the deserialized envelope yields the same bytes."""
        env = _make_envelope()
        data1 = to_canonical_json(env)
        env2 = from_canonical_json(data1)
        data2 = to_canonical_json(env2)
        assert data1 == data2


# ---------------------------------------------------------------------------
# Regression tests — code-review follow-up for Story 2.1
# ---------------------------------------------------------------------------


class TestRoundTripByteStableFromVariousInputs:
    """Fix B — datetime normalization at parse time makes canonical form a fixpoint."""

    def test_round_trip_byte_stable_on_plus_00_00_input(self) -> None:
        """Input with +00:00 suffix serializes byte-identically to Z-suffix output."""
        data_plus = (
            b'{"actor":{"id":"sys","kind":"system"},'
            b'"emitted_at":"2026-04-21T10:30:00.123+00:00",'
            b'"emitted_at_monotonic_ns":999,'
            b'"event_id":"e-01917e5c-a7d1-7000-8000-000000000001",'
            b'"parent_event_id":null,'
            b'"payload":{"value":"hello"},'
            b'"request_id":"01917e5c-a7d1-7000-8000-000000000002",'
            b'"schema_version":"1.0.0",'
            b'"trace_id":"01917e5c-a7d1-7000-8abc-000000000000",'
            b'"type":"task.created"}'
        )
        env = from_canonical_json(data_plus)
        canonical = to_canonical_json(env)
        # Re-parse + re-serialize → byte-identical (canonical form is the fixpoint).
        assert to_canonical_json(from_canonical_json(canonical)) == canonical

    def test_round_trip_byte_stable_on_microsecond_input(self) -> None:
        """Input with microsecond precision truncates to ms deterministically."""
        dt_us = datetime(2026, 4, 21, 10, 30, 0, 123456, tzinfo=UTC)
        env = _make_envelope(emitted_at=dt_us)
        data = to_canonical_json(env)
        # Sub-ms bits must be dropped: expect .123Z, never .123456 or similar.
        assert b".123456" not in data
        assert b"2026-04-21T10:30:00.123Z" in data
        # And round-trip is stable.
        assert to_canonical_json(from_canonical_json(data)) == data


class _StringPayload(BaseModel):
    s: str


class TestUnicodeEncodeErrorWrapped:
    """Fix G — UnicodeEncodeError on lone surrogates now raises CanonicalSerializationError."""

    def test_surrogate_string_raises_canonical_error(self) -> None:
        register("surrogate.test", "1.0.0", _StringPayload)
        env = EventEnvelope(
            event_id=_VALID_EVENT_ID,
            schema_version="1.0.0",
            type="surrogate.test",
            emitted_at=_VALID_EMITTED_AT,
            emitted_at_monotonic_ns=0,
            actor=Actor(kind="system", id="sys"),
            payload={"s": "\ud800"},  # lone high surrogate
            trace_id=_VALID_TRACE_ID,
            request_id=_VALID_REQUEST_ID,
        )
        with pytest.raises(CanonicalSerializationError):
            to_canonical_json(env)


class TestDatetimeNaiveErrorMessage:
    """Fix K — naive datetime now has a clear error message (not 'got offset None')."""

    def test_naive_datetime_message_says_naive(self) -> None:
        from events.canonical import _datetime_to_iso_z

        with pytest.raises(CanonicalSerializationError, match="naive"):
            _datetime_to_iso_z(datetime(2026, 4, 21, 10, 30, 0))


class TestBaseModelPayloadSerialization:
    """Story 3.5.4 — @field_serializer("payload") fixes Pydantic 2.12.5 union regression.

    Pydantic >=2.12 serializes ``dict[str, Any] | BaseModel`` unions
    incorrectly when the value is a BaseModel — the ``dict[str, Any]``
    branch produces ``{}`` instead of the model's fields. The
    ``_serialize_payload`` field serializer bypasses the union dispatch.
    """

    def test_basemodel_payload_fields_appear_in_output(self) -> None:
        env = _make_envelope(payload=_SimplePayload(value="from-model"))
        data = to_canonical_json(env)
        text = data.decode("utf-8")
        assert '"value":"from-model"' in text

    def test_basemodel_payload_round_trips(self) -> None:
        env = _make_envelope(payload=_SimplePayload(value="round-trip"))
        data = to_canonical_json(env)
        env2 = from_canonical_json(data)
        assert env2.payload == {"value": "round-trip"}

    def test_basemodel_payload_byte_stable(self) -> None:
        env = _make_envelope(payload=_SimplePayload(value="stable"))
        data1 = to_canonical_json(env)
        data2 = to_canonical_json(env)
        assert data1 == data2


# ---------------------------------------------------------------------------
# Story 9.7 pass-3 UH-8 — to_canonical_payload_json shared helper
# ---------------------------------------------------------------------------


class TestCanonicalPayloadJson:
    """Pass-3 UH-8: the shared payload encoder produces deterministic output
    for nested dicts, matching the rules ``to_canonical_json`` applies to the
    full envelope.
    """

    def test_payload_nested_dict_recursive_sort_keys(self) -> None:
        """sort_keys recurses — inner dicts are sorted, not just the top level."""
        data = {"b": {"z": 1, "a": 2}, "a": 1}
        out = to_canonical_payload_json(data)
        # Top-level sorted, AND nested dict also sorted.
        assert out == '{"a":1,"b":{"a":2,"z":1}}'

    def test_payload_byte_stable_across_input_orderings(self) -> None:
        """Two semantically-equal nested payloads byte-match after canonical encode."""
        nested_a = {"outer": {"z": 1, "a": 2}, "x": [3, 1, 2]}
        nested_b = {"x": [3, 1, 2], "outer": {"a": 2, "z": 1}}
        assert to_canonical_payload_json(nested_a) == to_canonical_payload_json(nested_b)

    def test_payload_no_whitespace_no_unicode_escape(self) -> None:
        """Canonical rules: no whitespace, ensure_ascii=False."""
        data = {"k": "v", "u": "тест"}
        out = to_canonical_payload_json(data)
        assert " " not in out
        assert "тест" in out  # non-ASCII preserved verbatim
        assert "\\u" not in out

    def test_payload_allow_nan_false_raises(self) -> None:
        """NaN / Inf floats raise CanonicalSerializationError, not silently null."""
        data = {"x": float("nan")}
        with pytest.raises(CanonicalSerializationError):
            to_canonical_payload_json(data)
