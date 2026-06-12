"""Compatibility shim for canonical event-type registrations.

Canonical payload models and ``ensure_registered()`` now live in the shared
``events.event_types`` module so non-service runtimes (for example MCP servers)
can install the same schema registry without importing across service
boundaries. This module intentionally re-exports that public surface for
legacy registry-state imports and preserves the import-time registration side
effect, including ``importlib.reload(registry_state.domain.event_types)``.
"""

from __future__ import annotations

from events import event_types as _event_types
from events.event_types import *  # noqa: F403 — compatibility re-export

__all__ = _event_types.__all__

# Preserve the historical module-load side effect from the original
# registry_state.domain.event_types implementation. Reloading this shim must
# also restore canonical registrations even when events.event_types is already
# present in sys.modules.
_event_types.ensure_registered()
