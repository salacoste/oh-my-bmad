"""Placeholder registry-api HTTP client — Story 4.2 fills in real calls."""

from __future__ import annotations

import httpx


class RegistryAPIClient:
    """Async HTTP client for registry-api.

    Story 4.2 adds real endpoint methods. This placeholder establishes
    the factory pattern (httpx.AsyncClient with base_url from settings).
    """

    def __init__(self, base_url: str = "http://registry-api:8080") -> None:
        self._base_url = base_url

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url)


__all__ = ["RegistryAPIClient"]
