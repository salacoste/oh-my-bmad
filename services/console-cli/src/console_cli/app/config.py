"""ConsoleSettings — pydantic-settings for console-cli."""

from __future__ import annotations

from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class ConsoleSettings(BaseSettings):
    """Console CLI configuration sourced from env-vars.

    Defaults point at docker-compose internal service names.
    Override for non-compose deployments (e.g., localhost).
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        extra="ignore",
    )

    # Plain str (not HttpUrl) — httpx accepts any string as base_url and
    # the CLI may point at non-standard targets (Unix sockets, etc.).
    registry_api_base_url: str = "http://registry-api:8080"


__all__ = ["ConsoleSettings"]
