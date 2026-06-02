"""Operator-side event-type registration site (Story 8.6 / FR56a).

This package hosts Pydantic payload models + ``register()`` side-effects for
event types whose primary EMITTER is NOT a long-running Platform service —
typically one-shot operator CLI helpers (``scripts/emit_*.py``) or offline
audit tooling.

**Why this directory exists**

``packages/events/src/events/schema_registry.py`` docstring anticipates two
registration sites: the ``packages/events/src/events/types/`` submodules
(this package), and ``<service>/domain/event_types.py`` (the owning-service
pattern, used by ``task.created`` et al. since Story 2.4).

The owning-service pattern was forced on ``task.created`` by the Story 3.9
H7 circular-import constraint (see ``schema_registry.py`` lines 97-103):
registry-state's ``adapters/event_log.py`` imports ``EventEnvelope`` at
module-load, and ``events.__init__`` is still in flight when that runs.

Story 8.6's event types have **no such cycle** — they're not consumed during
``events.__init__``. So they live here. Future Phase 2 stories adding new
operator-side event types (Epic 11 ``task.approval_signed``, Epic 13
``replication.lagging``) should follow this pattern.

**Import side effects**

Every submodule in this package registers ``(event_type, schema_version)``
tuples at module load via ``register()``. Importing the submodule (directly
or transitively via ``events.__init__``) is sufficient to make the event
available to ``EventEnvelope.create()``.
"""

from __future__ import annotations

# Side-effect import — registers ``deployment.signature_rejected`` (Story 8.6).
from events.types import deployment as _deployment  # noqa: F401

# Side-effect import — registers ``replication.lagging`` (Story 13.4 / NFR-R7).
from events.types import replication as _replication  # noqa: F401

__all__: list[str] = []
