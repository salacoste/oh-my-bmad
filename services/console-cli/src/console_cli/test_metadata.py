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
    CommandReadMetadata,
    mint_command_metadata,
    mint_poll_request_id,
    mint_read_metadata,
    mint_trace_id,
    mint_write_metadata,
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
    """CommandMetadata can be built directly for deterministic test fixtures.

    Pass-2 S4: switched fixture literals from ``"r-fixture"`` /
    ``"ik-fixture"`` to bare-UUIDv7 strings. Per ``events.ids``,
    ``new_request_id()`` and ``new_idempotency_key()`` both return
    BARE UUIDv7s (no prefix); the old prefixed fixtures suggested a
    nonexistent ``r-``/``ik-`` namespace that doesn't match production
    shape and would mislead future readers.
    """
    fake_request_id = "01917e5c-a7d1-7000-9abc-0123456789ab"
    fake_idempotency_key = "01917e5c-a7d1-7000-aabc-0123456789ab"
    metadata = CommandMetadata(
        request_id=fake_request_id,
        idempotency_key=fake_idempotency_key,
        trace_id=FAKE_TRACE_ID_UUIDV7,
    )
    assert metadata.request_id == fake_request_id
    assert metadata.idempotency_key == fake_idempotency_key
    assert metadata.trace_id == FAKE_TRACE_ID_UUIDV7


def test_mint_command_metadata_deterministic_under_frozen_clock() -> None:
    """R3 — clock+rng injection produces reproducible identifiers.

    Two calls to ``mint_command_metadata`` with the same ``FrozenClock`` +
    seeded ``Random`` must return byte-identical triples. This locks the
    injection contract: future refactors that drop the kwargs will break
    this test before they reach review.

    Pass-2 S3: the positive assertion alone is tautological — same
    inputs to a pure function always return the same outputs. Added a
    paired NEGATIVE assertion below that proves the ``rng`` kwarg is
    actually CONSUMED (a future refactor that silently dropped the
    kwarg from the body would still pass the positive case but fail
    the negative one).
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

    # Pass-2 S3 negative assertion: different rng → different output
    # proves the rng kwarg IS consumed. Without this, a refactor that
    # dropped ``rng=rng`` in the body would still green-light the
    # positive same-seed equality above (defaults would supply the
    # same OS RNG seed across both calls, since FrozenClock locks time).
    different_rng = mint_command_metadata(
        clock=FrozenClock(0, now=fixed_now), rng=Random(0xDEADBEEF)
    )
    assert different_rng.trace_id != first.trace_id, (
        "rng kwarg appears to be ignored by mint_command_metadata"
    )
    assert different_rng.request_id != first.request_id, (
        "rng kwarg appears to be ignored by mint_command_metadata"
    )
    assert different_rng.idempotency_key != first.idempotency_key, (
        "rng kwarg appears to be ignored by mint_command_metadata"
    )


def test_mint_poll_request_id_deterministic_under_frozen_clock() -> None:
    """R9 — the per-poll helper accepts the same clock/rng injection contract."""
    fixed_now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    seed = 0xBADCAFE
    first = mint_poll_request_id(clock=FrozenClock(0, now=fixed_now), rng=Random(seed))
    second = mint_poll_request_id(clock=FrozenClock(0, now=fixed_now), rng=Random(seed))
    assert first == second
    assert _UUIDV7_BARE_RE.match(first), first


# ---------------------------------------------------------------------------
# Pass-2 — new helpers added in batch follow-up
# ---------------------------------------------------------------------------


def test_mint_trace_id_returns_bare_uuidv7() -> None:
    """Pass-2 S5 — ``mint_trace_id`` returns a bare UUIDv7, NOT ``tg:`` form."""
    trace_id = mint_trace_id()
    assert is_valid_trace_id(trace_id), (
        f"trace_id {trace_id!r} must validate against Story 9.1 contract"
    )
    assert _UUIDV7_BARE_RE.match(trace_id), (
        f"trace_id {trace_id!r} must match bare UUIDv7 regex (not `tg:` form)"
    )
    assert not trace_id.startswith("tg:")


def test_mint_trace_id_returns_distinct_values_per_call() -> None:
    """Pass-2 S5 — two calls return two independent UUIDv7 values."""
    first = mint_trace_id()
    second = mint_trace_id()
    assert first != second


def test_mint_trace_id_deterministic_under_frozen_clock() -> None:
    """Pass-2 S5 — clock+rng injection produces reproducible trace_ids."""
    fixed_now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    seed = 0xFACADE
    first = mint_trace_id(clock=FrozenClock(0, now=fixed_now), rng=Random(seed))
    second = mint_trace_id(clock=FrozenClock(0, now=fixed_now), rng=Random(seed))
    assert first == second
    assert _UUIDV7_BARE_RE.match(first), first
    # Negative — different rng changes the output (kwarg is consumed).
    different = mint_trace_id(clock=FrozenClock(0, now=fixed_now), rng=Random(0xC0FFEE))
    assert different != first


def test_mint_read_metadata_returns_two_field_carrier() -> None:
    """Pass-2 S8 — ``mint_read_metadata`` returns a ``CommandReadMetadata``.

    Read-only GETs don't consume ``Idempotency-Key`` headers; the
    helper omits that mint and returns only ``(request_id, trace_id)``.
    """
    metadata = mint_read_metadata()
    assert isinstance(metadata, CommandReadMetadata)
    # Shape: both fields are bare UUIDv7.
    assert _UUIDV7_BARE_RE.match(metadata.request_id), metadata.request_id
    assert _UUIDV7_BARE_RE.match(metadata.trace_id), metadata.trace_id
    assert is_valid_trace_id(metadata.trace_id)
    # The two fields are independent (different mints).
    assert metadata.request_id != metadata.trace_id
    # The carrier is frozen — mutation must raise.
    with pytest.raises(FrozenInstanceError):
        metadata.trace_id = "tampered"  # type: ignore[misc]


def test_mint_write_metadata_alias_parity() -> None:
    """Pass-2 S8 — ``mint_command_metadata`` is a back-compat alias for ``mint_write_metadata``.

    Locks the alias so a future refactor that drops the back-compat
    name fails this test instead of silently breaking external imports.
    """
    fixed_now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    seed = 0xBEEF
    via_alias = mint_command_metadata(clock=FrozenClock(0, now=fixed_now), rng=Random(seed))
    via_canonical = mint_write_metadata(clock=FrozenClock(0, now=fixed_now), rng=Random(seed))
    assert via_alias == via_canonical


def test_uuidv7_bare_re_rejects_non_variant_bits() -> None:
    """Pass-2 S12 — RFC 9562 §4 variant must be ``10xx`` (8/9/a/b); reject c-f.

    Positive tests already lock the ``[89ab]`` variant byte; this is
    the matching negative case: the ``c``-prefixed variant byte
    represents a NON-RFC-9562 layout (variant bits ``11xx``) and must
    NOT match the bare-UUIDv7 regex.
    """
    invalid = "01917e5c-a7d1-7000-cabc-0123456789ab"
    assert not _UUIDV7_BARE_RE.match(invalid), (
        f"variant byte 'c' (binary 1100) must be rejected; matched {invalid!r}"
    )
    # Also check 'd', 'e', 'f' for completeness.
    for variant_byte in ("d", "e", "f"):
        candidate = f"01917e5c-a7d1-7000-{variant_byte}abc-0123456789ab"
        assert not _UUIDV7_BARE_RE.match(candidate), (
            f"variant byte {variant_byte!r} (non-RFC-9562) must be rejected; matched"
        )
