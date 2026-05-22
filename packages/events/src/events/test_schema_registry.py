"""Unit tests for events.schema_registry — register/lookup/cache behavior.

AC-6 / Story 2.1: ~8 tests. Every test calls unregister_all() in setup to
isolate state (global mutable registry).
"""

from __future__ import annotations

from collections.abc import Generator
from typing import cast

import pytest
from pydantic import BaseModel
from registry_state.domain.event_types import (  # noqa: IMP001 — Story 8.7.5: restore canonical registry state after unregister_all() teardown so sibling tests don't see an empty registry
    ensure_registered,
)

import events.schema_registry as sr
from events.schema_registry import (
    REGISTRY,
    _rebuild_types_cache,
    register,
    unregister_all,
)


class _PayloadA(BaseModel):
    a: int


class _PayloadB(BaseModel):
    b: str


class _PayloadC(BaseModel):
    c: float


@pytest.fixture(autouse=True)
def _clean_registry() -> Generator[None, None, None]:
    unregister_all()
    yield
    unregister_all()
    # Story 8.7.5 — restore canonical registry state after teardown so
    # tests running later in the session don't see an empty registry.
    ensure_registered()


class TestRegisterNewType:
    def test_registry_and_event_types_updated(self) -> None:
        register("task.created", "1.0.0", _PayloadA)
        assert ("task.created", "1.0.0") in REGISTRY
        assert REGISTRY[("task.created", "1.0.0")] is _PayloadA
        assert "task.created" in sr.EVENT_TYPES


class TestRegisterIdempotent:
    def test_same_model_reregister_is_noop(self) -> None:
        register("task.created", "1.0.0", _PayloadA)
        register("task.created", "1.0.0", _PayloadA)  # second call — no-op
        assert len(REGISTRY) == 1

    def test_event_types_unchanged_after_reregister(self) -> None:
        register("task.created", "1.0.0", _PayloadA)
        register("task.created", "1.0.0", _PayloadA)
        assert frozenset({"task.created"}) == sr.EVENT_TYPES


class TestRegisterConflict:
    def test_different_model_raises_value_error(self) -> None:
        register("task.created", "1.0.0", _PayloadA)
        with pytest.raises(ValueError, match="already registered"):
            register("task.created", "1.0.0", _PayloadB)


class TestEventTypesCacheUnversioned:
    def test_two_versions_same_type_one_entry_in_event_types(self) -> None:
        register("task.created", "1.0.0", _PayloadA)
        register("task.created", "2.0.0", _PayloadB)
        assert frozenset({"task.created"}) == sr.EVENT_TYPES
        assert len(REGISTRY) == 2


class TestEventTypesAfterUnregisterAll:
    def test_both_cleared(self) -> None:
        register("task.created", "1.0.0", _PayloadA)
        unregister_all()
        assert len(REGISTRY) == 0
        assert frozenset() == sr.EVENT_TYPES


class TestRegisterMultipleDomains:
    def test_three_types_in_event_types(self) -> None:
        register("task.created", "1.0.0", _PayloadA)
        register("task.updated", "1.0.0", _PayloadB)
        register("task.deleted", "1.0.0", _PayloadC)
        assert frozenset({"task.created", "task.updated", "task.deleted"}) == sr.EVENT_TYPES


class TestRebuildTypesCacheManual:
    def test_direct_mutation_then_rebuild(self) -> None:
        # Directly mutate REGISTRY (tests the helper, not the public API)
        REGISTRY[("manual.event", "1.0.0")] = _PayloadA
        _rebuild_types_cache()
        assert "manual.event" in sr.EVENT_TYPES


# ---------------------------------------------------------------------------
# Regression tests — code-review follow-up for Story 2.1
# ---------------------------------------------------------------------------


class TestEventTypesCrossModuleLiveBinding:
    """Fix A — importing ``EVENT_TYPES`` by name captured a stale frozenset."""

    def test_schema_registry_attribute_reflects_updates(self) -> None:
        """After register(), importers of schema_registry see the update via attribute access."""
        register("live.bound", "1.0.0", _PayloadA)
        assert "live.bound" in sr.EVENT_TYPES

    def test_events_package_reexport_is_live(self) -> None:
        """``from events import EVENT_TYPES`` also reflects live state via PEP 562 __getattr__."""
        import events

        register("live.bound.two", "1.0.0", _PayloadA)
        event_types = cast(frozenset[str], events.EVENT_TYPES)
        assert "live.bound.two" in event_types


class TestRegisterRejectsInvalidShape:
    """Fix D — register() now validates event_type and schema_version shape."""

    def test_rejects_uppercase_type(self) -> None:
        with pytest.raises(ValueError, match="dotted lowercase past-tense"):
            register("Bad.Type", "1.0.0", _PayloadA)

    def test_rejects_type_with_no_dot(self) -> None:
        with pytest.raises(ValueError, match="dotted"):
            register("nosuffix", "1.0.0", _PayloadA)

    def test_rejects_empty_type(self) -> None:
        with pytest.raises(ValueError, match="dotted"):
            register("", "1.0.0", _PayloadA)

    def test_rejects_non_semver_schema_version(self) -> None:
        with pytest.raises(ValueError, match="semver"):
            register("valid.type", "not-a-semver", _PayloadA)

    def test_rejects_two_segment_semver(self) -> None:
        with pytest.raises(ValueError, match="semver"):
            register("valid.type", "1.0", _PayloadA)

    def test_rejects_four_segment_semver(self) -> None:
        with pytest.raises(ValueError, match="semver"):
            register("valid.type", "1.0.0.0", _PayloadA)
