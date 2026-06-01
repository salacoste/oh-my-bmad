"""idempotency — UUIDv7 idempotency-key generation + cachetools.TTLCache + SQLite durability. 7-day retention per FR28.

Story 1.2 ships only ``__version__``. Story 2.7 ships the cache library
(TTLCache + SQLite durability).  Story 3.6 ships the FastAPI middleware
integration.
"""

from __future__ import annotations

from idempotency.cache import (
    CacheHit,
    IdempotencyCacheStore,
    create_idempotency_schema,
)
from idempotency.errors import IdempotencyConflict

__version__ = "0.2.0"

__all__ = [
    "CacheHit",
    "IdempotencyCacheStore",
    "IdempotencyConflict",
    "__version__",
    "create_idempotency_schema",
]
