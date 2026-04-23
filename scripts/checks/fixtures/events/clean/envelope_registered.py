"""Fixture: EventEnvelope(type=...) with a registered literal — should pass."""
from __future__ import annotations


def make() -> None:
    EventEnvelope(  # noqa: F821 — fixture-only; real EventEnvelope lands in Story 2.1
        type="task.created",
        payload={},
    )
