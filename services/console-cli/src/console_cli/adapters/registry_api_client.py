"""Placeholder registry-api HTTP client — Story 4.2 fills in real calls."""

from __future__ import annotations

import httpx


class RegistryAPIClient:
    """Async HTTP client for registry-api.

    Story 4.2 adds real endpoint methods. This placeholder establishes
    the factory pattern (httpx.AsyncClient with base_url from settings).

    Story 4.2 MUST replace ``_client()`` with a cached, properly closed
    instance. httpx.AsyncClient holds a connection pool; creating one per
    call without closing leaks TCP connections. Use ``async with`` or
    store as an instance attribute with explicit ``aclose()``.
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    def _client(self) -> httpx.AsyncClient:
        """Return a new AsyncClient — caller owns the lifecycle."""
        return httpx.AsyncClient(base_url=self._base_url)


__all__ = ["RegistryAPIClient"]
