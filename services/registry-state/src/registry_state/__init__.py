"""registry-state — Event-sourced task + session registry (single writer per FR26; SQLite WAL materialized state; event log is source of truth).

Story 1.2 ships only `__version__`. Real logic arrives in: Stories 2.3–2.7 (SQLite schema, event-log writer, materializer, snapshots, idempotency cache).
"""

__version__ = "0.1.0"
