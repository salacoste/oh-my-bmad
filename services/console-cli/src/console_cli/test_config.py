"""Tests for ConsoleSettings defaults (Story 4.1 AC-8)."""

from __future__ import annotations

import pytest

from console_cli.app.config import ConsoleSettings


def test_default_registry_api_base_url() -> None:
    """Default base URL points at docker-compose service name."""
    settings = ConsoleSettings()
    assert settings.registry_api_base_url == "http://registry-api:8080"


def test_custom_registry_api_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env-var override works."""
    monkeypatch.setenv("REGISTRY_API_BASE_URL", "http://localhost:9999")
    settings = ConsoleSettings()
    assert settings.registry_api_base_url == "http://localhost:9999"
