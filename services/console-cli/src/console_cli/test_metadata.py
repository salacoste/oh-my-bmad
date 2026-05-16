"""Tests for ``console_cli.app.metadata`` — Story 9.4 AC6 mint helpers."""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from random import Random

import pytest
from events.clock import FrozenClock
from events.envelope import is_valid_trace_id

from console_cli._test_fixtures import FAKE_TRACE_ID_UUIDV7, UUIDV7_BARE_RE_PATTERN
from console_cli.app.metadata import (
    CommandMetadata,
    mint_command_metadata,
    mint_poll_request_id,
)

# Bare UUIDv7 regex — must match the (non-`tg:`) branch of is_valid_trace_id.
# Sourced from the shared test-fixtures module (R15) so any drift in the
# canonical pattern fails ALL tests at once rather than divergently.
_UUIDV7_BARE_RE = re.compile(UUIDV7_BARE_RE_PATTERN)


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
    """AC6 — CommandMetadata is a frozen dataclass; mutation raises.

    R4: tighten to ``FrozenInstanceError`` only — accepting ``AttributeError``
    would silently green-light a refactor that swapped ``frozen=True`` for
    ``__slots__`` (which raises ``AttributeError`` on attribute set rather
    than enforcing immutability semantically).
    """
    metadata = mint_command_metadata()
    with pytest.raises(FrozenInstanceError):
        metadata.trace_id = "tampered"  # type: ignore[misc]


def test_command_metadata_explicit_construction() -> None:
    """CommandMetadata can be built directly for deterministic test fixtures."""
    metadata = CommandMetadata(
        request_id="r-fixture",
        idempotency_key="ik-fixture",
        trace_id=FAKE_TRACE_ID_UUIDV7,
    )
    assert metadata.request_id == "r-fixture"
    assert metadata.idempotency_key == "ik-fixture"
    assert metadata.trace_id == FAKE_TRACE_ID_UUIDV7


def test_mint_command_metadata_deterministic_under_frozen_clock() -> None:
    """R3 — clock+rng injection produces reproducible identifiers.

    Two calls to ``mint_command_metadata`` with the same ``FrozenClock`` +
    seeded ``Random`` must return byte-identical triples. This locks the
    injection contract: future refactors that drop the kwargs will break
    this test before they reach review.
    """
    fixed_now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    clock_a = FrozenClock(0, now=fixed_now)
    rng_a = Random(0xC0FFEE)
    first = mint_command_metadata(clock=clock_a, rng=rng_a)

    clock_b = FrozenClock(0, now=fixed_now)
    rng_b = Random(0xC0FFEE)
    second = mint_command_metadata(clock=clock_b, rng=rng_b)

    assert first.request_id == second.request_id
    assert first.idempotency_key == second.idempotency_key
    assert first.trace_id == second.trace_id
    # Shape sanity — the deterministic value still validates as bare UUIDv7.
    assert _UUIDV7_BARE_RE.match(first.trace_id), first.trace_id


def test_mint_poll_request_id_deterministic_under_frozen_clock() -> None:
    """R9 — the per-poll helper accepts the same clock/rng injection contract."""
    fixed_now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    seed = 0xBADCAFE
    first = mint_poll_request_id(clock=FrozenClock(0, now=fixed_now), rng=Random(seed))
    second = mint_poll_request_id(clock=FrozenClock(0, now=fixed_now), rng=Random(seed))
    assert first == second
    assert _UUIDV7_BARE_RE.match(first), first
