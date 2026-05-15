"""Shared event envelope + schema registry + canonical serializer for the oh-my-bmad platform.

Story 2.1 lands the real model + registry + serializer. Story 1.1 shipped the
__version__ stub; Story 1.6 shipped the schema-registry frozenset stub.
Subsequent stories (2.2 UUIDv7+clock, 2.3+ registry, 5.x workers) build on
this module's contracts.
"""

from __future__ import annotations

from events import schema_registry as _schema_registry
from events.canonical import from_canonical_json, to_canonical_json
from events.clock import FROZEN_EPOCH, Clock, FrozenClock, SystemClock, TickingClock
from events.envelope import Actor, EventEnvelope
from events.errors import (
    CanonicalSerializationError,
    CapabilityDenied,
    EventSchemaUnknown,
    EventsError,
    EventValidationError,
    WorktreeLockHeld,
)
from events.ids import (
    new_decision_id,
    new_event_id,
    new_idempotency_key,
    new_request_id,
    new_session_id,
    new_task_id,
    new_uuid7,
    new_worker_id,
    parse_prefix,
)

# Story 3.5.2 — re-export payload models so consumers can use
# ``from events import TaskCreatedPayload`` instead of cross-service imports.
from events.payloads import *  # noqa: F403 — intentional star re-export
from events.payloads import __all__ as _payloads_all
from events.schema_registry import REGISTRY, register

# Story 8.6 — side-effect import: registers operator-side event types
# (``deployment.signature_rejected`` and future Epic-11/13 additions). See
# ``events/types/__init__.py`` for the pattern rationale.
#
# Code-review fix F14: the explicit ``from events.types import deployment``
# below is RETAINED as defense-in-depth even though ``events.types.__init__``
# already triggers the side-effect on its own. Both paths register
# idempotently (``schema_registry.py:80`` no-op for same-model re-register),
# and the redundancy guards against future refactors that might import
# ``events.types.deployment`` directly without going through
# ``events.types``. Tracker: review during Epic 11 when more
# ``events/types/<X>.py`` files arrive.
from events.types import deployment as _types_deployment  # noqa: F401
from events.types.deployment import DeploymentSignatureRejectedPayload

__version__ = "0.4.0"


def __getattr__(name: str) -> object:
    """Module-level __getattr__ (PEP 562) for live-binding re-exports.

    ``EVENT_TYPES`` in ``events.schema_registry`` is rebound on every
    ``register()`` call (see ``_rebuild_types_cache``). If we imported it
    here eagerly, downstream consumers of ``from events import EVENT_TYPES``
    (and ``events.EVENT_TYPES`` attribute access) would capture the initial
    empty frozenset and never see updates. Resolving on attribute access
    always returns the current value.
    """
    if name == "EVENT_TYPES":
        return _schema_registry.EVENT_TYPES
    raise AttributeError(f"module 'events' has no attribute {name!r}")


__all__ = [
    "EVENT_TYPES",  # noqa: F405 — resolved lazily via __getattr__
    "FROZEN_EPOCH",
    "REGISTRY",
    "Actor",
    "CanonicalSerializationError",
    "CapabilityDenied",
    "Clock",
    "DeploymentSignatureRejectedPayload",
    "EventEnvelope",
    "EventSchemaUnknown",
    "EventValidationError",
    "EventsError",
    "WorktreeLockHeld",
    "FrozenClock",
    "SystemClock",
    "TickingClock",
    "__version__",
    "from_canonical_json",
    "new_decision_id",
    "new_event_id",
    "new_idempotency_key",
    "new_request_id",
    "new_session_id",
    "new_task_id",
    "new_uuid7",
    "new_worker_id",
    "parse_prefix",
    "register",
    "to_canonical_json",
    *_payloads_all,
]
