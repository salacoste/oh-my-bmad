"""mTLS settings model (Phase 11 / ADR-0023).

Reads certificate paths from ``os.environ`` directly — no
``pydantic-settings`` dependency to keep the transport package lightweight,
mirroring the convention established in ``mcp_auth.settings``.

Design notes
------------
* **All-or-nothing (P11-I1).**  When ``enabled`` is ``True`` ALL three
  paths (cert, key, CA) must be non-empty.  The ``model_validator`` enforces
  this so a partially configured service fails fast at startup rather than
  silently falling back to plaintext.
* **``from_env`` classmethod** — single entry-point for env-var resolution,
  matching the ``McpAuthSettings`` pattern from Phase 10.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, model_validator

from mtls._exceptions import MTLSConfigError

_TRUTHY = frozenset({"true", "1", "yes"})


class MTLSSettings(BaseModel):
    """mTLS configuration sourced from environment variables.

    Construct via :py:meth:`MTLSSettings.from_env` so env-var resolution
    happens in one well-known place.
    """

    enabled: bool = False
    cert_path: str | None = None
    key_path: str | None = None
    ca_path: str | None = None
    rotation_warning_hours: int = 24

    @model_validator(mode="after")
    def _require_all_paths_when_enabled(self) -> MTLSSettings:
        """When mTLS is enabled, cert / key / CA paths must all be present."""
        if not self.enabled:
            return self
        missing: list[str] = []
        if not self.cert_path:
            missing.append("MTLS_CERT_PATH")
        if not self.key_path:
            missing.append("MTLS_KEY_PATH")
        if not self.ca_path:
            missing.append("MTLS_CA_PATH")
        if missing:
            raise MTLSConfigError(
                "mTLS is enabled but the following required environment "
                "variables are missing or empty: " + ", ".join(missing)
            )
        return self

    @classmethod
    def from_env(cls) -> MTLSSettings:
        """Construct from the process environment.

        Reads ``MTLS_ENABLED``, ``MTLS_CERT_PATH``, ``MTLS_KEY_PATH``,
        ``MTLS_CA_PATH``, and ``MTLS_ROTATION_WARNING_HOURS`` directly
        from ``os.environ``.
        """
        raw_enabled = os.environ.get("MTLS_ENABLED", "").strip().lower()
        raw_hours = os.environ.get("MTLS_ROTATION_WARNING_HOURS", "").strip()
        return cls(
            enabled=raw_enabled in _TRUTHY,
            cert_path=(v.strip() or None) if (v := os.environ.get("MTLS_CERT_PATH")) else None,
            key_path=(v.strip() or None) if (v := os.environ.get("MTLS_KEY_PATH")) else None,
            ca_path=(v.strip() or None) if (v := os.environ.get("MTLS_CA_PATH")) else None,
            rotation_warning_hours=int(raw_hours) if raw_hours else 24,
        )
