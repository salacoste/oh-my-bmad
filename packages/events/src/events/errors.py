"""Typed exception hierarchy for event-envelope + schema-registry errors.

Stories that emit events import these and raise them via the MCP surface
(Story 2.8 clawhip-bridge + Story 2.4 registry-state writer). Story 2.1 only
defines the classes; runtime emission of `event.unknown_schema` events lands
in Story 2.4.
"""

from __future__ import annotations


class EventsError(Exception):
    """Base class for all event-envelope / schema-registry errors."""


class EventSchemaUnknown(EventsError):  # noqa: N818
    """Raised when EventEnvelope.create() sees an unregistered (type, version)."""

    def __init__(
        self,
        event_type: str,
        schema_version: str,
        registered_types: frozenset[str],
    ) -> None:
        self.event_type = event_type
        self.schema_version = schema_version
        self.registered_types = registered_types
        super().__init__(self._format())

    def _format(self) -> str:
        known = ", ".join(sorted(self.registered_types)) or "(empty registry)"
        return (
            f"unknown event schema ({self.event_type!r}, {self.schema_version!r}); "
            f"registered types: {known}"
        )


class EventValidationError(EventsError):
    """Wraps Pydantic ValidationError with platform-facing formatting."""

    def __init__(self, message: str, *, pydantic_error: Exception | None = None) -> None:
        self.pydantic_error = pydantic_error
        super().__init__(message)


class CanonicalSerializationError(EventsError):
    """Raised when canonical serialization fails (NaN/Inf, non-serializable payload)."""
