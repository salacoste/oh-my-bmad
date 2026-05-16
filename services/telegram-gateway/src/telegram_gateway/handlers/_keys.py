"""Shared idempotency-key helpers for all decision-command handlers (Story 3.4+).

Public helpers shared by /approve, /stop, /reject, /retry handlers
(Stories 3.4, 3.16, 3.17, 3.18).

Idempotency-key derivation
--------------------------
Same UUIDv5→UUIDv7 reshape as Story 3.3 H1.  The key is a UUIDv5 derived
from _TELEGRAM_NAMESPACE_UUID and seed "{chat_id}:{message_id}", then
**reshaped** so the version nibble reads ``7`` and the variant nibble reads
``10xx``.  This satisfies registry-api's IdempotencyKeyMiddleware UUIDv7
regex::

    \\A[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\\Z

WARNING: do NOT revert to the plain-string format "telegram-{chat_id}-{message_id}".
That format fails the regex and silently creates duplicate tasks on Telegram
retries (Story 3.3 H1).

Task-id validation
------------------
TASK_ID_PATTERN validates the UUIDv7 shape with "t-" prefix per architecture
naming rules.  Future stories must import from here, not define locally.
"""

from __future__ import annotations

import re
import uuid

from aiogram.types import Message

# Fixed namespace UUID encoding the "tg:" Telegram-service discriminator.
# Reuses RFC 4122 URL namespace UUID (6ba7b810-9dad-11d1-80b4-00c04fd430c8)
# as a stable base — the seed string "{chat_id}:{message_id}" makes it
# Telegram-specific via UUIDv5 derivation.  A Slack gateway with the same
# numeric ids would use a different namespace UUID.
_TELEGRAM_NAMESPACE_UUID = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

# Compiled regex for the registry-api IdempotencyKeyMiddleware UUIDv7 shape.
# Reused by tests to assert keys pass middleware validation without calling it.
#
# Story 9.2 pass-1 review B3 (mirror-update discipline): anchors tightened
# from ``^...$`` to ``\A...\Z`` to match the envelope-side validator at
# ``packages/events/src/events/envelope.py:134``. Python's ``re.match`` only
# anchors start; ``$`` matches before a trailing ``\n`` so a hostile value of
# ``<valid-uuid>\n<garbage>`` would previously slip past ``^...$`` validation.
# ``\A`` / ``\Z`` anchor strictly to absolute start/end of the input.
UUIDV7_BARE_RE: re.Pattern[str] = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)

# Compiled regex for the "t-<uuidv7>" task-id format.
# All handlers that accept a <task-id> argument MUST import from here.
# Story 9.2 pass-1 review B3: same ``\A`` / ``\Z`` tightening as above.
TASK_ID_PATTERN: re.Pattern[str] = re.compile(
    r"\At-[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)


def idempotency_key_from_message(message: Message) -> str:
    """Derive a deterministic UUIDv7-shaped idempotency key from (chat_id, message_id).

    Strategy (Story 3.3 H1 / FR28):

    1. Compute ``uuid.uuid5(_TELEGRAM_NAMESPACE_UUID, f"{chat_id}:{message_id}")``.
    2. Reshape the version nibble to ``7`` and the variant nibble to ``10xx``
       (standard RFC 4122 variant) so the result satisfies registry-api's
       ``IdempotencyKeyMiddleware`` UUIDv7 regex.

    The reshape is deterministic: the same ``(chat_id, message_id)`` always
    produces the same key.  Different pairs produce different keys (UUIDv5
    guarantees collision-resistance within the namespace).

    The seed is ``"{chat_id}:{message_id}"`` — NOT ``"{chat_id}:{message_id}:{action}"``.
    The ``message_id`` is unique per Telegram message; the command IS that message.
    Adding action to the seed would allow a client bug to re-use the same message
    with a different action and bypass idempotency.

    WARNING: do not revert to the plain-string format
    "telegram-{chat_id}-{message_id}" — that format fails registry-api's
    IdempotencyKeyMiddleware UUIDv7 regex and silently creates duplicate tasks
    on Telegram retries (Story 3.3 H1).

    WARNING: The reshaped UUIDv7 satisfies registry-api's regex but does NOT
    preserve UUIDv7's monotonic-time-order property — bytes 0-5 are SHA-1
    derived, not timestamp. This is a shape-compatible idempotency key, not a
    sortable timestamp. If you need monotonic ordering, use a fresh ``uuid7()``
    and pass it through. (Story 3.4 M5)
    """
    seed = uuid.uuid5(_TELEGRAM_NAMESPACE_UUID, f"{message.chat.id}:{message.message_id}")
    # Reshape to UUIDv7: set version nibble to 7, variant nibble to 10xx.
    reshaped = bytearray(seed.bytes)
    reshaped[6] = (reshaped[6] & 0x0F) | 0x70  # version = 7
    reshaped[8] = (reshaped[8] & 0x3F) | 0x80  # variant = 10xx
    return str(uuid.UUID(bytes=bytes(reshaped)))


def extract_task_id_from_message(message: Message) -> str | None:
    """Parse a command message and validate the task-id argument against UUIDv7 shape.

    Splits on whitespace with a maximum of 1 split so ``/approve t-xxx trailing``
    causes the candidate to be ``t-xxx trailing`` which fails TASK_ID_PATTERN,
    correctly rejecting trailing garbage (Story 3.4 M1).

    Returns the task-id string if valid, None otherwise.
    Rejects uppercase hex, t-less IDs, and non-UUIDv7 version nibbles.

    Stories 3.4, 3.16, 3.17, 3.18 all import this function — do NOT define
    a local copy in any handler file.
    """
    parts = (message.text or "").split(None, 1)
    if len(parts) < 2:
        return None
    candidate = parts[1]
    return candidate if TASK_ID_PATTERN.match(candidate) else None


def extract_task_id_with_trailing(message: Message) -> tuple[str | None, str | None]:
    """Parse a command message into a validated task-id plus optional trailing free-text.

    Splits on whitespace with a maximum of 2 splits, producing up to
    ``[command, task_id, trailing_text]``.  This is the 3-part variant
    needed by ``/reject <task-id> [reason]`` and ``/retry <task-id> [hint]``
    which accept optional free-text after the task-id.

    Unlike :func:`extract_task_id_from_message` (which uses ``split(None, 1)``
    to reject trailing garbage), this function *preserves* the trailing text
    so the caller can apply its own policy (e.g. truncation, length limits).

    Returns ``(task_id, trailing_text)`` where *trailing_text* is the
    stripped remainder after the task-id, or ``None`` if absent.
    Returns ``(None, None)`` when the task-id is missing or fails validation.
    """
    raw_text = message.text or ""
    parts = raw_text.split(None, 2)
    if len(parts) < 2 or not TASK_ID_PATTERN.match(parts[1]):
        return (None, None)
    task_id = parts[1]
    trailing = parts[2].strip() if len(parts) >= 3 else None
    return (task_id, trailing)


__all__ = [
    "TASK_ID_PATTERN",
    "UUIDV7_BARE_RE",
    "extract_task_id_from_message",
    "extract_task_id_with_trailing",
    "idempotency_key_from_message",
]
