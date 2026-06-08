"""Async bridge — Typer sync → asyncio.run() (Story 4.2 AC-5)."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine


def run_async[T](coro: Coroutine[object, object, T]) -> T:
    """Bridge Typer's synchronous interface to the async httpx client."""
    return asyncio.run(coro)
