"""Shared event envelope + schema registry + canonical serializer for the oh-my-bmad platform.

Story 2.1 lands the real model + registry + serializer. Story 1.1 shipped the
__version__ stub; Story 1.6 shipped the schema-registry frozenset stub.
Subsequent stories (2.2 UUIDv7+clock, 2.3+ registry, 5.x workers) build on
this module's contracts.
"""

from __future__ import annotations

from events import schema_registry as _schema_registry
from events._filesystem import ensure_shared_dir
from events.approval_signing import compute_approval_hmac, compute_key_fingerprint
from events.backfill import backfill_trace_id_from_request_id
from events.canonical import from_canonical_json, to_canonical_json
from events.clock import FROZEN_EPOCH, Clock, FrozenClock, SystemClock, TickingClock
from events.envelope import Actor, EventEnvelope
from events.errors import (
    CanonicalSerializationError,
    CapabilityDenied,
    CursorSchemaVersionError,
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
from events.log_reader import (
    EventLogReader,
    current_day_path,
    iter_new_envelopes_since,
    parse_with_pre110_backfill,
    read_log_lines,
    read_new_envelopes_since,
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
    "CursorSchemaVersionError",
    # Story 11.4 PP3 — pure HMAC signer relocated from
    # services/registry-api/src/registry_api/adapters/approval_signing.py.
    # The registry-api module now re-exports this symbol as a thin
    # compatibility shim. New callers (including scripts/verify_approval.py)
    # MUST import from events directly so the offline verifier does not
    # transitively pull FastAPI / SQLAlchemy / Anthropic.
    "compute_approval_hmac",
    # Story 11.5 AC1 — pure key-fingerprint helper alongside compute_approval_hmac.
    # SSoT placement per Story 11.5 D2; verifier (Story 11.4) may import later.
    "compute_key_fingerprint",
    # Story 11.2 — no explicit entries added: KeyRotatedPayload and
    # CapabilityDeniedPayload ride in solely via ``*_payloads_all`` spliced
    # at the bottom of __all__ (see ``__all__ += _payloads_all`` below).
    # The pre-existing ``CapabilityDenied`` entry on line 90 is an unrelated
    # exception class imported from ``events.errors`` — NOT the new
    # ``CapabilityDeniedPayload`` (P1-L2 disambiguation).
    "DeploymentSignatureRejectedPayload",
    "EventEnvelope",
    "EventLogReader",
    "EventSchemaUnknown",
    "EventValidationError",
    "EventsError",
    "WorktreeLockHeld",
    "FrozenClock",
    "SystemClock",
    "TickingClock",
    "__version__",
    # Story 9.7 pass-3 UH-9: public re-export of the shared back-fill helper
    # so callers can use ``from events import backfill_trace_id_from_request_id``
    # instead of the deeper ``from events.backfill import ...`` form.
    "backfill_trace_id_from_request_id",
    "current_day_path",
    # Story 11.3.8 — shared-volume directory helper for mkdir + chmod 2775
    # at every EventLogWriter / event_log_dir creation site. Mirrors the
    # registry-state ``_ensure_db_parent_dir`` pattern; closes the
    # production fresh-deploy permission regression discovered during
    # Story 11.3.7 Task 7.
    "ensure_shared_dir",
    "from_canonical_json",
    "iter_new_envelopes_since",
    "new_decision_id",
    "new_event_id",
    "new_idempotency_key",
    "new_request_id",
    "new_session_id",
    "new_task_id",
    "new_uuid7",
    "new_worker_id",
    "parse_prefix",
    "parse_with_pre110_backfill",
    "read_log_lines",
    "read_new_envelopes_since",
    "register",
    "to_canonical_json",
    *_payloads_all,
]
