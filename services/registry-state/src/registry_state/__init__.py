"""registry-state — Event-log subscriber + state materializer + SQLite store. Single writer per FR26.

Story 1.2 ships only `__version__`. Real logic arrives in: Stories 2.3–2.7 (SQLite schema, event-log writer, materializer, snapshots, idempotency cache).
"""

__version__ = "0.1.0"
