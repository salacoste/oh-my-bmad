"""Central registry of every (event_type, schema_version) → payload_model the platform may emit.

REGISTRY starts EMPTY. Every future story that defines a new event type extends
it via ``register()`` in that story's own initialization code (typically a
submodule under ``packages/events/src/events/types/`` OR the owning service's
domain layer). Story 2.1 ships the infrastructure + empty starting state; the
first real event type lands in Story 2.4 (``task.created`` for the event-log
writer).

Two public data structures are exported:

- ``REGISTRY`` — the full ``(type, version) → payload_model`` map; used at
  ``EventEnvelope.create()`` time to validate payload shape.
- ``EVENT_TYPES`` — a convenience ``frozenset`` of just the type-name strings
  (any version). Used by ``scripts/check_event_registry.py`` which scans
  emission-site ``type="..."`` literals but doesn't know the schema_version.

Frozen at stub-time (Story 1.6) became ``REGISTRY: frozenset[str] = frozenset()``;
Story 2.1 upgrades to the real dict shape. Future stories ``register()``;
``EVENT_TYPES`` is auto-rebuilt.
"""

from __future__ import annotations

from pydantic import BaseModel

# Mutable — per-story additions via register().
REGISTRY: dict[tuple[str, str], type[BaseModel]] = {}

# Convenience: type names only (any version).
EVENT_TYPES: frozenset[str] = frozenset()


def _rebuild_types_cache() -> None:
    """Recompute EVENT_TYPES from REGISTRY's current keys."""
    global EVENT_TYPES
    EVENT_TYPES = frozenset(event_type for event_type, _version in REGISTRY)


def register(
    event_type: str,
    schema_version: str,
    payload_model: type[BaseModel],
) -> None:
    """Register a (type, version) → payload_model triple.

    Idempotent: re-registering the SAME model for the SAME (type, version)
    is a no-op. Registering a DIFFERENT model for an existing key raises
    ``ValueError`` — per NFR-M3 event-schema evolution is additive-only
    within a major version; replacing an existing payload model in place
    violates that.
    """
    key = (event_type, schema_version)
    existing = REGISTRY.get(key)
    if existing is None:
        REGISTRY[key] = payload_model
        _rebuild_types_cache()
        return
    if existing is payload_model:
        return  # idempotent same-model re-register
    raise ValueError(
        f"event schema {key!r} already registered to {existing.__name__}; "
        f"cannot rebind to {payload_model.__name__} (NFR-M3 additive-only)"
    )


def unregister_all() -> None:
    """Test-only helper: clear the entire registry + cache.

    Production code must never call this. Unit tests that manipulate the
    registry use this to reset state between test functions.
    """
    REGISTRY.clear()
    _rebuild_types_cache()
