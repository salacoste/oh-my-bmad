"""Re-export shim for the event-log writer and reader symbols.

Write-side symbols (``EventLogWriter``, ``InMemoryEventLogWriter``,
``recover_all_logs``) were relocated to ``packages/events/src/events/event_log_writer.py``
so cross-service consumers can import from the shared ``events`` package
without cross-service import violations.

Read-side symbols (``current_day_path``, ``read_log_lines``,
``read_new_envelopes_since``, ``parse_with_pre110_backfill``, ``EventLogReader``)
remain in ``packages/events/src/events/log_reader.py``.

This module re-exports everything for backwards compatibility. Existing
call-sites within registry-state (and any external code importing from
``registry_state.adapters.event_log``) continue to work unchanged.
"""

from __future__ import annotations

# Write-side symbols — relocated to the shared events package.
from events.event_log_writer import (
    EventLogWriter,
    InMemoryEventLogWriter,
    recover_all_logs,
)

# Read-side symbols — re-exported from the events package.
from events.log_reader import (
    EventLogReader,
    current_day_path,
    parse_with_pre110_backfill,
    read_log_lines,
    read_new_envelopes_since,
)

__all__ = [
    "EventLogReader",  # Story 10.2 AC1 re-export
    "EventLogWriter",
    "InMemoryEventLogWriter",
    "current_day_path",
    "parse_with_pre110_backfill",
    "read_log_lines",
    "read_new_envelopes_since",
    "recover_all_logs",
]
