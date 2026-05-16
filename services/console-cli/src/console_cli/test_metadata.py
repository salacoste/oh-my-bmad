"""Tests for ``console_cli.app.metadata`` — Story 9.4 AC6 mint helpers."""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError

import pytest
from events.envelope import is_valid_trace_id

from console_cli.app.metadata import CommandMetadata, mint_command_metadata

# Bare UUIDv7 regex — must match the (non-`tg:`) branch of is_valid_trace_id.
# Mirrors AC9's literal pattern verbatim so any drift fails this test.
_UUIDV7_BARE_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def test_mint_command_metadata_returns_uuidv7_trace_id() -> None:
    """AC4 + AC6 #1 — minted trace_id is a bare UUIDv7, NOT ``tg:`` form."""
    metadata = mint_command_metadata()

    assert is_valid_trace_id(metadata.trace_id), (
        f"trace_id {metadata.trace_id!r} must validate against Story 9.1 contract"
    )
    assert _UUIDV7_BARE_RE.match(metadata.trace_id), (
        f"trace_id {metadata.trace_id!r} must match bare UUIDv7 regex (not `tg:` form)"
    )
    assert not metadata.trace_id.startswith("tg:"), (
        "console-cli trace_id must be bare UUIDv7 — `tg:` is Telegram-exclusive (Story 9.3)"
    )


def test_mint_command_metadata_returns_distinct_values_per_call() -> None:
    """AC6 #2 — two calls return three independent UUIDv7 values each."""
    first = mint_command_metadata()
    second = mint_command_metadata()

    assert first.request_id != second.request_id
    assert first.idempotency_key != second.idempotency_key
    assert first.trace_id != second.trace_id
    # Within a single CommandMetadata, the three fields are also distinct
    # (independent UUIDv7s — collision would imply a generator bug).
    assert first.request_id != first.idempotency_key
    assert first.request_id != first.trace_id
    assert first.idempotency_key != first.trace_id


def test_command_metadata_is_frozen() -> None:
    """AC6 — CommandMetadata is a frozen dataclass; mutation raises."""
    metadata = mint_command_metadata()
    with pytest.raises((FrozenInstanceError, AttributeError)):
        metadata.trace_id = "tampered"  # type: ignore[misc]


def test_command_metadata_explicit_construction() -> None:
    """CommandMetadata can be built directly for deterministic test fixtures."""
    metadata = CommandMetadata(
        request_id="r-fixture",
        idempotency_key="ik-fixture",
        trace_id="01917e5c-a7d1-7000-8abc-0123456789ab",
    )
    assert metadata.request_id == "r-fixture"
    assert metadata.idempotency_key == "ik-fixture"
    assert metadata.trace_id == "01917e5c-a7d1-7000-8abc-0123456789ab"
