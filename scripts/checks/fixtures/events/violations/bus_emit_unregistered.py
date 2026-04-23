"""Fixture: clawhip.emit(type="not.in.registry") — should fail EVT001."""
from __future__ import annotations


def run() -> None:
    clawhip.emit(  # noqa: F821 — fixture-only
        type="not.in.registry",
        payload={},
    )
