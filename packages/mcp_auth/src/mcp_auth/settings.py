"""JWT auth settings for MCP Streamable HTTP transport (Phase 10).

Mirrors ``JwtAuthSettings`` from ``registry-api`` but simplified for the MCP
context: no ``access_token_expire_minutes`` (token generation is owned by
registry-api), no ``pydantic-settings`` dependency (the MCP transport package
stays lightweight — ``from_env`` reads ``os.environ`` directly).

Design notes:

* **HS256 symmetric** — Phase 10 uses a shared secret (HMAC-SHA256) matching
  the registry-api convention.  The same ``JWT_SECRET_KEY`` is shared between
  the token-issuing service (registry-api) and this validating middleware.
* **``SecretStr`` wrapping** — masks the value in ``repr()`` / ``model_dump()``
  / log records (NFR-S10 isolation).
* **``default=None``** — a missing key is NOT a startup error.  When ``None``,
  auth is disabled and the middleware passes requests through (stdio transport
  never mounts this middleware, so the passthrough is a safety net for
  misconfigured deployments).
* **32 bytes minimum** — enforced via ``_normalize`` byte-count check,
  matching the cryptographic invariant used by ``JwtAuthSettings``.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, SecretStr, field_validator


class McpAuthSettings(BaseModel):
    """JWT configuration for MCP bearer token validation (Phase 10).

    Construct via :py:meth:`McpAuthSettings.from_env` so the env-var
    resolution happens in one well-known place.
    """

    jwt_secret_key: SecretStr | None = None
    algorithm: str = "HS256"
    issuer: str = "oh-my-bmad/registry-api"
    leeway_seconds: int = 30

    @property
    def enabled(self) -> bool:
        """True when JWT auth is configured (secret key is non-empty, >=32 bytes)."""
        if self.jwt_secret_key is None:
            return False
        raw = self.jwt_secret_key.get_secret_value()
        return bool(raw.strip()) and len(raw.encode("utf-8")) >= 32

    @field_validator("jwt_secret_key", mode="after")
    @classmethod
    def _normalize(cls, v: SecretStr | None) -> SecretStr | None:
        """Normalise empty/whitespace to ``None``; enforce >=32 bytes when set."""
        if v is None:
            return None
        raw = v.get_secret_value()
        if not raw.strip():
            return None
        if len(raw.encode("utf-8")) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 BYTES (UTF-8 encoded) "
                "/ 256 bits minimum when set "
                f"(got {len(raw.encode('utf-8'))} bytes); "
                "recommend 64-char hex from `openssl rand -hex 32`"
            )
        return v

    @classmethod
    def from_env(cls) -> McpAuthSettings:
        """Construct from the process environment.

        Reads ``JWT_SECRET_KEY`` directly from ``os.environ`` — no
        ``pydantic-settings`` dependency to keep the MCP transport package
        lightweight.
        """
        raw = os.environ.get("JWT_SECRET_KEY")
        return cls(
            jwt_secret_key=SecretStr(raw) if raw is not None else None,
        )
