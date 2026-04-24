"""Fixture registry for check_event_registry self-test (clean case).

Exports EVENT_TYPES to match the Story-2.1 schema_registry public interface.
"""

EVENT_TYPES: frozenset[str] = frozenset({"task.created"})
