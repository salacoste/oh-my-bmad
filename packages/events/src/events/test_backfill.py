"""Tests for :mod:`events.backfill` (Story 9.7 pass-2 TH-B5 / pass-3 UH-5).

Pass-3 UH-5: schema_version provenance contract — back-fill must NOT
silently up-tag empty / null / garbage values to ``"1.1.0"``. Only the
three historical wire labels (``1.0.0``, ``1.0.1``, ``1.1.0``) are
accepted as "already explicit"; anything else is tagged ``"1.0.0"``
(the historical default) on back-fill so provenance survives.
"""

from __future__ import annotations

from events.backfill import backfill_trace_id_from_request_id

_VALID_TRACE_ID = "01917e5c-a7d1-7000-8abc-000000000001"


def test_backfill_already_valid_returns_input() -> None:
    """No-op when trace_id is valid AND schema_version is an explicit wire label."""
    input_dict = {
        "trace_id": _VALID_TRACE_ID,
        "schema_version": "1.1.0",
        "request_id": _VALID_TRACE_ID,
    }
    result = backfill_trace_id_from_request_id(input_dict)
    assert result is input_dict, "no-op path should return the input dict unchanged (same object)"


def test_backfill_missing_trace_id_uses_request_id() -> None:
    """trace_id missing, request_id is bare UUIDv7 → trace_id populated."""
    record = {"request_id": _VALID_TRACE_ID, "schema_version": "1.0.0"}
    result = backfill_trace_id_from_request_id(record)
    assert result is not None
    assert result["trace_id"] == _VALID_TRACE_ID
    assert result["schema_version"] == "1.0.0"


def test_backfill_e_prefixed_request_id_stripped() -> None:
    """e-<uuidv7> request_id (pre-9.1 shape) → bare UUIDv7 as trace_id."""
    record = {"request_id": f"e-{_VALID_TRACE_ID}", "schema_version": "1.0.0"}
    result = backfill_trace_id_from_request_id(record)
    assert result is not None
    assert result["trace_id"] == _VALID_TRACE_ID


def test_backfill_missing_schema_version_tagged_1_0_0() -> None:
    """Pass-3 UH-5: missing schema_version → tagged "1.0.0" (historical default)."""
    record = {"trace_id": _VALID_TRACE_ID, "request_id": _VALID_TRACE_ID}
    result = backfill_trace_id_from_request_id(record)
    assert result is not None
    assert result["schema_version"] == "1.0.0"


def test_backfill_null_schema_version_tagged_1_0_0_not_silently_upgraded() -> None:
    """Pass-3 UH-5: explicit ``schema_version: null`` is tagged "1.0.0".

    Before UH-5 this case was silently upgraded to the EventEnvelope default
    (``"1.1.0"``), falsifying provenance. The fix: any non-wire-label value
    (including ``None``) is overwritten to ``"1.0.0"`` so the wire label
    matches the record's true historical origin.
    """
    record = {"trace_id": _VALID_TRACE_ID, "schema_version": None, "request_id": _VALID_TRACE_ID}
    result = backfill_trace_id_from_request_id(record)
    assert result is not None
    assert result["schema_version"] == "1.0.0", (
        "null schema_version must be tagged '1.0.0', not silently upgraded"
    )


def test_backfill_empty_string_schema_version_tagged_1_0_0() -> None:
    """Pass-3 UH-5: empty string schema_version → tagged "1.0.0".

    Before UH-5, ``isinstance("", str)`` was True so empty was passed through
    unchanged, crashing downstream validation with a less-actionable error.
    """
    record = {"trace_id": _VALID_TRACE_ID, "schema_version": "", "request_id": _VALID_TRACE_ID}
    result = backfill_trace_id_from_request_id(record)
    assert result is not None
    assert result["schema_version"] == "1.0.0", (
        "empty-string schema_version must be tagged '1.0.0', not passed through"
    )


def test_backfill_garbage_schema_version_tagged_1_0_0() -> None:
    """Pass-3 UH-5: arbitrary garbage string → tagged "1.0.0".

    Unknown wire labels are rewritten so downstream validation gets the
    historical default rather than failing with a confusing schema-version
    mismatch.
    """
    record = {
        "trace_id": _VALID_TRACE_ID,
        "schema_version": "garbage",
        "request_id": _VALID_TRACE_ID,
    }
    result = backfill_trace_id_from_request_id(record)
    assert result is not None
    assert result["schema_version"] == "1.0.0"


def test_backfill_known_wire_labels_preserved() -> None:
    """Pass-3 UH-5: all three historical wire labels pass through unchanged."""
    for label in ("1.0.0", "1.0.1", "1.1.0"):
        record = {
            "trace_id": _VALID_TRACE_ID,
            "schema_version": label,
            "request_id": _VALID_TRACE_ID,
        }
        result = backfill_trace_id_from_request_id(record)
        assert result is not None
        assert result["schema_version"] == label, (
            f"wire label {label!r} must round-trip unchanged; got {result['schema_version']!r}"
        )


def test_backfill_unrecoverable_record_returns_none() -> None:
    """No valid trace_id AND no valid request_id → caller must decide what to do."""
    record = {"schema_version": "1.0.0"}
    result = backfill_trace_id_from_request_id(record)
    assert result is None


def test_backfill_invalid_request_id_shape_returns_none() -> None:
    """Request_id that doesn't match UUIDv7-or-e-UUIDv7 → None."""
    record = {"request_id": "not-a-uuid", "schema_version": "1.0.0"}
    result = backfill_trace_id_from_request_id(record)
    assert result is None
