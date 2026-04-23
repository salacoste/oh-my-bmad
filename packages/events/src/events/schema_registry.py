"""Central registry of every event type the platform may emit.

Entries are added alongside the first emission site for each event (Story 2.1
adds the initial `task.created` / `task.completed` etc.). The
`scripts/check_event_registry.py` CI gate verifies every literal `type=`
argument at emission sites is present here.

Frozen at stub-time to signal the registry is append-only within a major
schema version (Architecture §Category 1 / NFR-M3 additive-only rule).
"""

from __future__ import annotations

REGISTRY: frozenset[str] = frozenset()
