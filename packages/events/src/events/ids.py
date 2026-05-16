"""UUIDv7 generators + prefixed-ID helpers (Arch §line-308-310 / NFR-O1).

RFC 9562 v7 bit layout:

  - Bits 0-47:   unix_ts_ms  (big-endian, 48 bits)
  - Bits 48-51:  version     (4 bits, value 0b0111 = 7)
  - Bits 52-63:  rand_a      (12 bits)
  - Bits 64-65:  variant     (2 bits, value 0b10)
  - Bits 66-127: rand_b      (62 bits)

Total random entropy = 74 bits. All generators accept optional ``clock`` +
``rng`` injection for deterministic testing — when both are ``None``, they
fall back to ``time.time()`` and ``os.urandom`` respectively.
"""

from __future__ import annotations

import os
import re
import time
from datetime import UTC, datetime, timedelta
from random import Random

from events.clock import Clock

_UNIX_EPOCH: datetime = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_MILLISECOND: timedelta = timedelta(milliseconds=1)
# Story 9.2 pass-2 review N4 (mirror-update L2 discipline): anchors
# tightened from ``^...$`` to ``\A...\Z`` to close the same trailing-newline
# bypass class as Story 9.1 F1 (envelope-side) and Story 9.2 B3
# (telegram-gateway ``_keys.py``). Python's ``re.match`` only anchors start;
# ``$`` matches before a trailing ``\n`` so a hostile value of
# ``t-<valid-uuid>\n<garbage>`` could previously pass ``parse_prefix``.
# ``\A``/``\Z`` anchor strictly to the absolute start/end of the input.
_UUIDV7_BARE_RE = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)


def new_uuid7(
    *,
    clock: Clock | None = None,
    rng: Random | None = None,
) -> str:
    """Generate an RFC 9562 v7 UUID as canonical hyphenated lowercase hex.

    Output always matches regex:
      ``^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$``
    """
    if clock is not None:
        now = clock.now()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError(
                f"Clock.now() must return UTC-aware datetime; got tzinfo={now.tzinfo!r}"
            )
        ts_ms = (now - _UNIX_EPOCH) // _ONE_MILLISECOND
    else:
        ts_ms = int(time.time() * 1000)
    if not (0 <= ts_ms < (1 << 48)):
        raise ValueError(f"timestamp {ts_ms} out of 48-bit range")

    rand_bytes = rng.randbytes(10) if rng is not None else os.urandom(10)
    rand_a = int.from_bytes(rand_bytes[0:2], "big") & 0x0FFF
    rand_b = int.from_bytes(rand_bytes[2:10], "big") & ((1 << 62) - 1)

    uuid_int = (ts_ms << 80) | (0b0111 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    h = f"{uuid_int:032x}"
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def new_task_id(
    *,
    clock: Clock | None = None,
    rng: Random | None = None,
) -> str:
    """Prefixed UUIDv7 for a persistent Task entity (Arch §line-308)."""
    return f"t-{new_uuid7(clock=clock, rng=rng)}"


def new_session_id(
    *,
    clock: Clock | None = None,
    rng: Random | None = None,
) -> str:
    """Prefixed UUIDv7 for a persistent Session entity."""
    return f"s-{new_uuid7(clock=clock, rng=rng)}"


def new_worker_id(
    *,
    clock: Clock | None = None,
    rng: Random | None = None,
) -> str:
    """Prefixed UUIDv7 for a Worker entity."""
    return f"w-{new_uuid7(clock=clock, rng=rng)}"


def new_event_id(
    *,
    clock: Clock | None = None,
    rng: Random | None = None,
) -> str:
    """Prefixed UUIDv7 for an event. Matches envelope.py ``event_id`` validator."""
    return f"e-{new_uuid7(clock=clock, rng=rng)}"


def new_idempotency_key(
    *,
    clock: Clock | None = None,
    rng: Random | None = None,
) -> str:
    """Bare UUIDv7 for an idempotency key (Arch §line-309 — no prefix)."""
    return new_uuid7(clock=clock, rng=rng)


def new_request_id(
    *,
    clock: Clock | None = None,
    rng: Random | None = None,
) -> str:
    """Bare UUIDv7 for an HTTP X-Request-ID (Arch §line-309 — no prefix)."""
    return new_uuid7(clock=clock, rng=rng)


def new_decision_id(
    *,
    clock: Clock | None = None,
    rng: Random | None = None,
) -> str:
    """Prefixed UUIDv7 for an operator decision (Story 6.4 / FR7)."""
    return f"d-{new_uuid7(clock=clock, rng=rng)}"


def parse_prefix(s: str) -> tuple[str, str] | None:
    """If ``s`` is ``"<prefix>-<uuidv7>"``, return (prefix, uuid_core); else None.

    Only recognizes the canonical prefixes: ``t-``, ``s-``, ``e-``, ``w-``, ``d-``. The UUID
    core is validated against the canonical UUIDv7 regex — malformed UUIDs
    return ``None`` even when prefixed correctly.

    Non-str inputs (including ``None``) return ``None`` rather than raising.
    """
    if not isinstance(s, str):
        return None
    if len(s) < 2 or s[1] != "-":
        return None
    prefix = s[0]
    if prefix not in {"t", "s", "e", "w", "d"}:
        return None
    rest = s[2:]
    if not _UUIDV7_BARE_RE.match(rest):
        return None
    return (prefix, rest)


__all__ = [
    "new_decision_id",
    "new_event_id",
    "new_idempotency_key",
    "new_request_id",
    "new_session_id",
    "new_task_id",
    "new_uuid7",
    "new_worker_id",
    "parse_prefix",
]
