"""Fixture: clawhip.emit(type=...) with a registered literal — should pass."""
from __future__ import annotations


def run() -> None:
    clawhip.emit(  # noqa: F821 — fixture-only; real clawhip module lands in Story 2.8
        type="task.created",
        payload={},
    )
