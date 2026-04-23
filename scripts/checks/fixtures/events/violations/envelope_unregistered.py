"""Fixture: EventEnvelope(type="not.in.registry") — should fail EVT001."""
from __future__ import annotations


def make() -> None:
    EventEnvelope(  # noqa: F821 — fixture-only
        type="not.in.registry",
        payload={},
    )
