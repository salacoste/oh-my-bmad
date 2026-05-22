"""Fixture: unregister_all() followed by explicit register() calls — should pass RI001."""

from __future__ import annotations

from events.schema_registry import register, unregister_all


def teardown_registry() -> None:
    unregister_all()
    # Re-register canonical types explicitly — accepted restore pattern.
    register("task.created", "1.0.0", object)
    register("task.updated", "1.0.0", object)
