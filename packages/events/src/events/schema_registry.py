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

import re

from pydantic import BaseModel

# Mutable — per-story additions via register().
REGISTRY: dict[tuple[str, str], type[BaseModel]] = {}

# Convenience: type names only (any version).
EVENT_TYPES: frozenset[str] = frozenset()

# Shape validators enforced at register() time — fail-fast so typos at
# registration sites do not poison EVENT_TYPES. Mirror the regexes in
# envelope.py (``_EVENT_TYPE_RE`` / ``_SEMVER_RE``); intentionally duplicated
# to avoid an import cycle (envelope imports this module).
#
# Story 9.1 code-review N1 (pass-2): use ``\A``/``\Z`` (not ``^``/``$``) so
# trailing-newline inputs can't sneak past at registration time either. The
# envelope-side fix (commit 1ea5e90) explicitly promised these would stay
# in sync; pass-1 missed the mirror, pass-2 closes it.
_EVENT_TYPE_RE = re.compile(r"\A[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+\Z")
_SEMVER_RE = re.compile(r"\A[0-9]+\.[0-9]+\.[0-9]+\Z")


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

    Validates ``event_type`` and ``schema_version`` shape per the envelope
    rules (Architecture §Format Patterns + §Naming Patterns) so typos at
    registration sites fail fast rather than poisoning ``EVENT_TYPES``.

    Idempotent: re-registering the SAME model for the SAME (type, version)
    is a no-op. Registering a DIFFERENT model for an existing key raises
    ``ValueError`` — per NFR-M3 event-schema evolution is additive-only
    within a major version; replacing an existing payload model in place
    violates that.
    """
    if not _EVENT_TYPE_RE.match(event_type):
        raise ValueError(
            f"event_type {event_type!r} must be dotted lowercase past-tense "
            f"(pattern: {_EVENT_TYPE_RE.pattern})"
        )
    if not _SEMVER_RE.match(schema_version):
        raise ValueError(f"schema_version {schema_version!r} must be semver MAJOR.MINOR.PATCH")
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


# Story 3.9 AC-11 / H7 NOTE: ``task.created`` schema versions (1.0.0, 1.0.1,
# 1.1.0) are all registered by ``registry_state.domain.event_types`` at its
# own module-load time. A deferred packages→services import here would
# trigger a circular-import cycle (``events.__init__`` is still in flight
# when ``registry_state.adapters.event_log`` runs ``from events import
# EventEnvelope``). The service-side registration is the single source of
# truth; this module exposes only the registry primitive.
#
# Story 8.6 / FR56a NOTE: ``deployment.signature_rejected`` (and future
# operator-side event types in Phase 2 — Epic 11 ``task.approval_signed``,
# Epic 13 ``replication.lagging``) register from the
# ``packages/events/src/events/types/<domain>.py`` submodules instead. No
# circular-import cycle there: operator-side events have no service-layer
# emitter, so the registration site stays inside ``packages/events``. See
# ``events/types/__init__.py`` for the rationale.
