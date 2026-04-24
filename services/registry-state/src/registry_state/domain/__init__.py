"""registry_state.domain — domain logic for the registry-state service.

Story 2.5 ships:
  - MaterializerError: typed exception for state-transition failures.
  - Materializer: event-log → SQLite state dispatch core.
  - event_types: 4 payload models + schema-registry registrations.
  - handlers: 4 state-transition handler functions.
"""

from registry_state.domain.errors import MaterializerError

__all__ = ["MaterializerError"]
