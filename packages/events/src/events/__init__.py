"""Shared event envelope + schema registry + canonical serializer for the oh-my-bmad platform.

Story 2.1 lands the real model + registry + serializer. Story 1.1 shipped the
__version__ stub; Story 1.6 shipped the schema-registry frozenset stub.
Subsequent stories (2.2 UUIDv7+clock, 2.3+ registry, 5.x workers) build on
this module's contracts.
"""

from __future__ import annotations

from events.canonical import from_canonical_json, to_canonical_json
from events.envelope import Actor, EventEnvelope
from events.errors import (
    CanonicalSerializationError,
    EventSchemaUnknown,
    EventsError,
    EventValidationError,
)
from events.schema_registry import EVENT_TYPES, REGISTRY, register

__version__ = "0.2.0"

__all__ = [
    "EVENT_TYPES",
    "REGISTRY",
    "Actor",
    "CanonicalSerializationError",
    "EventEnvelope",
    "EventSchemaUnknown",
    "EventValidationError",
    "EventsError",
    "__version__",
    "from_canonical_json",
    "register",
    "to_canonical_json",
]
