"""Per-invocation correlation identifiers minted at console-cli command entry.

Each `oh-my-bmad-cli <command>` invocation mints a fresh triple of
identifiers — `request_id`, `idempotency_key`, and `trace_id` — used to
correlate the outbound HTTP call to registry-api with downstream event-spine
records.

Centralising the mint logic here keeps the 10 command modules in sync: when
a future identifier joins the triple, only this helper changes.

The `trace_id` is a **bare UUIDv7** per Story 9.1's shape contract. The
``tg:<update_id>`` form is reserved for the Telegram ingress (Story 9.3).

Idempotency replay caveat
-------------------------
Every invocation of ``mint_command_metadata`` returns fresh identifiers.
If registry-api's idempotency cache (Story 6.4) returns a cached response
for a duplicate request (same ``Idempotency-Key``), the persisted envelope's
``trace_id`` reflects the **original** invocation, not the current retry.
An operator searching by the retry's ``trace_id`` via Story 9.7's
``oh-my-bmad-cli trace <id>`` query will find zero events for the replayed
call; the lookup must fall back to the shared ``idempotency_key``.
"""

from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import TYPE_CHECKING

from events import new_idempotency_key, new_request_id
from events.ids import new_uuid7

if TYPE_CHECKING:
    from events.clock import Clock


@dataclass(frozen=True)
class CommandMetadata:
    """Per-invocation correlation identifiers minted at command entry.

    Validation contract
    -------------------
    ``CommandMetadata`` is a **transport-only carrier with no value
    validation**. Callers that construct it via :func:`mint_command_metadata`
    receive bare-UUIDv7 strings validated by construction; tests that
    instantiate the dataclass directly are responsible for shape
    conformance themselves. Runtime validation in ``__post_init__``
    would add overhead to every command invocation, so the design
    accepts the trade-off: production callsites always go through the
    helper, and the helper is the single source of truth for shape.

    Attributes:
        request_id: Per-call request correlation id (UUIDv7), forwarded
            as ``X-Request-ID`` to registry-api.
        idempotency_key: Per-call idempotency key (UUIDv7), forwarded
            as ``Idempotency-Key`` to registry-api for write endpoints.
        trace_id: Bare UUIDv7 trace identifier per Story 9.1's contract,
            forwarded as ``X-Trace-Id`` to registry-api so the event spine
            can link the command-originated envelope to its origin.
    """

    request_id: str
    idempotency_key: str
    trace_id: str


@dataclass(frozen=True)
class CommandReadMetadata:
    """Correlation identifiers for **read-only** GET commands (pass-2 S8).

    Subset of :class:`CommandMetadata` containing only the two identifiers
    that GET endpoints consume — ``request_id`` (forwarded as
    ``X-Request-ID``) and ``trace_id`` (forwarded as ``X-Trace-Id``).
    The ``idempotency_key`` field is omitted because HTTP GET is
    idempotent by semantics; registry-api never inspects an
    ``Idempotency-Key`` header on read endpoints.

    Splitting the read and write carriers makes the semantic clear at
    the callsite: ``mint_read_metadata()`` for ``status`` / ``logs`` /
    ``events`` (non-follow) / ``ping`` / ``agent``;
    ``mint_write_metadata()`` for ``task`` / ``approve`` / ``reject`` /
    ``stop`` / ``retry``.

    Validation contract is identical to :class:`CommandMetadata` —
    transport-only carrier, callers via :func:`mint_read_metadata`
    receive bare-UUIDv7 strings by construction.

    Attributes:
        request_id: Per-call request correlation id (bare UUIDv7).
        trace_id: Bare UUIDv7 trace identifier per Story 9.1's contract.
    """

    request_id: str
    trace_id: str


def mint_write_metadata(
    *,
    clock: Clock | None = None,
    rng: Random | None = None,
) -> CommandMetadata:
    """Mint a fresh ``(request_id, idempotency_key, trace_id)`` triple for writes.

    Called once per **write** command invocation (POST endpoints —
    ``task`` / ``approve`` / ``reject`` / ``stop`` / ``retry``). All
    three fields are independent bare-UUIDv7 strings; the ``trace_id``
    validates against the UUIDv7 branch of
    ``events.envelope.is_valid_trace_id``.

    Args:
        clock: Optional ``Clock`` to inject for deterministic minting
            (e.g. ``FrozenClock`` in tests). When ``None``, the default
            wall clock is used.
        rng: Optional ``random.Random`` instance to inject for deterministic
            byte generation. When ``None``, the default RNG is used.

    Idempotency caveat:
        If registry-api dedupes a duplicate write via the
        ``Idempotency-Key`` cache, the persisted envelope's ``trace_id``
        reflects the **original** invocation, not the current retry.
        Story 9.7's ``oh-my-bmad-cli trace`` query should fall back to
        ``idempotency_key`` lookup when reconciling retried events.
    """
    return CommandMetadata(
        request_id=new_request_id(clock=clock, rng=rng),
        idempotency_key=new_idempotency_key(clock=clock, rng=rng),
        trace_id=new_uuid7(clock=clock, rng=rng),
    )


def mint_read_metadata(
    *,
    clock: Clock | None = None,
    rng: Random | None = None,
) -> CommandReadMetadata:
    """Mint a fresh ``(request_id, trace_id)`` pair for **read-only** commands (pass-2 S8).

    Called once per **read** command invocation (GET endpoints —
    ``status`` / ``logs`` / ``events`` non-follow / ``ping`` / ``agent``).
    Omits the ``idempotency_key`` mint that ``mint_write_metadata``
    eagerly builds — GET endpoints don't consume an ``Idempotency-Key``
    header.

    Args:
        clock: Optional ``Clock`` for deterministic minting.
        rng: Optional ``random.Random`` for deterministic minting.
    """
    return CommandReadMetadata(
        request_id=new_request_id(clock=clock, rng=rng),
        trace_id=new_uuid7(clock=clock, rng=rng),
    )


def mint_command_metadata(
    *,
    clock: Clock | None = None,
    rng: Random | None = None,
) -> CommandMetadata:
    """Backwards-compatible alias for :func:`mint_write_metadata`.

    Retained so external callers that imported the original name during
    pass-1 continue to work. New write callsites should prefer
    :func:`mint_write_metadata` for symmetry with :func:`mint_read_metadata`.
    """
    return mint_write_metadata(clock=clock, rng=rng)


def mint_trace_id(
    *,
    clock: Clock | None = None,
    rng: Random | None = None,
) -> str:
    """Mint a fresh bare-UUIDv7 ``trace_id`` (no request_id/idempotency_key) — pass-2 S5.

    Use for callers that only need the parent ``trace_id`` correlation —
    e.g. the ``events.py --follow`` polling loop, where idempotency-key
    doesn't apply (GET endpoint) and ``request_id`` is minted per-poll
    via :func:`mint_poll_request_id`. Avoids wasting two-thirds of the
    triple that :func:`mint_command_metadata` eagerly produces.

    Args:
        clock: Optional ``Clock`` for deterministic minting.
        rng: Optional ``random.Random`` for deterministic minting.
    """
    return new_uuid7(clock=clock, rng=rng)


def mint_poll_request_id(
    *,
    clock: Clock | None = None,
    rng: Random | None = None,
) -> str:
    """Mint a fresh ``request_id`` for a per-iteration poll within a command.

    Separate from :func:`mint_command_metadata` — the polling loop in
    ``events.py --follow`` reuses the parent ``trace_id`` (per-command
    correlation) but mints a fresh ``request_id`` per HTTP attempt
    (per-call correlation). Splitting this off keeps the mint surface
    centralised here so future identifiers joining the per-poll triple
    don't require touching command modules.

    Args:
        clock: Optional ``Clock`` for deterministic minting.
        rng: Optional ``random.Random`` for deterministic minting.
    """
    return new_request_id(clock=clock, rng=rng)


__all__ = [
    "CommandMetadata",
    "CommandReadMetadata",
    "mint_command_metadata",
    "mint_poll_request_id",
    "mint_read_metadata",
    "mint_trace_id",
    "mint_write_metadata",
]
