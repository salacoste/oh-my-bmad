"""Idempotency-cache error types (Story 2.7).

``IdempotencyConflict`` is raised by ``IdempotencyCacheStore.store()`` on a
primary-key collision that survives the SQLite ``on_conflict_do_nothing``
UPSERT.

In a single-writer deployment (FR26) this should NEVER fire — a collision means
two distinct store() calls for the same key raced past the per-key asyncio.Lock
in ``get_or_run``. That represents a hard invariant violation: either the lock
was bypassed (bug) or two separate processes are writing (FR26 violation).

When it *does* fire, the caller must re-invoke ``get(key)`` to retrieve the
winner's ``CacheHit``.
"""

from __future__ import annotations


class IdempotencyConflict(Exception):  # noqa: N818 — name reflects the event-type identifier convention (no Error suffix); see Story 2.1's EventSchemaUnknown precedent
    """Raised when ``store()`` detects a primary-key collision for *key*.

    Attributes:
        key: The idempotency key that collided.
    """

    def __init__(self, key: str) -> None:
        super().__init__(
            f"Idempotency key collision: {key!r}. "
            "Re-invoke get() to retrieve the winning CacheHit. "
            "In single-writer mode (FR26) this is a hard invariant violation."
        )
        self.key = key


__all__ = ["IdempotencyConflict"]
