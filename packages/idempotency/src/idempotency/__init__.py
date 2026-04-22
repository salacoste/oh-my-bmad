"""idempotency — UUIDv7 idempotency-key generation + cachetools.TTLCache + SQLite durability. 7-day retention per FR28.

Story 1.2 ships only `__version__`. Real logic arrives in: Stories 2.7 (cache implementation) + 3.6 (FastAPI middleware integration).
"""

__version__ = "0.1.0"
