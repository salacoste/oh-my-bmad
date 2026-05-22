"""Root-level pytest conftest for the oh-my-bmad monorepo.

Centralizes session-scoped fixtures that ALL tests across the repo
(services/, packages/, tests/) need. Currently:

- ``_ensure_event_types_registered`` — session-scoped autouse that calls
  ``registry_state.domain.event_types.ensure_registered()`` exactly
  once at session start. Replaces the per-file fixture pattern introduced
  by Story 7e4ffec; this is the Story 8.7.5 consolidation.
"""

from __future__ import annotations

import pytest

# Story 8.7.5 — register all event types once per session.
# NOTE: this import is cross-service (root conftest imports from services/) but
# is the canonical location for the registry — same pattern as the per-file
# fixtures we're replacing.
from registry_state.domain.event_types import ensure_registered  # noqa: IMP001


@pytest.fixture(scope="session", autouse=True)
def _ensure_event_types_registered() -> None:
    """Story 8.7.5 — call ensure_registered() exactly once per test session.

    Tests that `unregister_all()` mid-run (e.g., packages/events/test_canonical.py)
    are responsible for restoring registry state at function-scope; this
    session-scoped fixture does NOT auto-restore between tests.
    """
    ensure_registered()
