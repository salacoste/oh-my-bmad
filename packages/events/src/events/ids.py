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
import time
from random import Random

from events.clock import Clock


def new_uuid7(
    *,
    clock: Clock | None = None,
    rng: Random | None = None,
) -> str:
    """Generate an RFC 9562 v7 UUID as canonical hyphenated lowercase hex.

    Output always matches regex:
      ``^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$``
    """
    ts_ms = int(clock.now().timestamp() * 1000) if clock is not None else int(time.time() * 1000)
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


def parse_prefix(s: str) -> tuple[str, str] | None:
    """If ``s`` is ``"<prefix>-<uuidv7>"``, return (prefix, uuid_core); else None.

    Only recognizes the canonical prefixes: ``t-``, ``s-``, ``e-``.
    Bare UUIDv7 returns ``None`` (no prefix to parse).
    """
    if "-" not in s or len(s) < 2:
        return None
    prefix, _, rest = s.partition("-")
    if prefix in {"t", "s", "e"} and len(rest) == 36:
        return (prefix, rest)
    return None


__all__ = [
    "new_event_id",
    "new_idempotency_key",
    "new_request_id",
    "new_session_id",
    "new_task_id",
    "new_uuid7",
    "parse_prefix",
]
