"""HTTP correlation header constants for registry-api communication.

Centralised so consumers (``adapters.registry_api_client``,
``commands.events``) all share the same literal strings without
duplicating magic constants. Co-locating with ``app/`` (rather than
``adapters/``) inverts the dependency direction: commands depend on
``app.headers`` directly, not on adapter internals (pass-2 S6).
"""

from __future__ import annotations

from typing import Final

REQUEST_ID_HEADER: Final[str] = "X-Request-ID"
TRACE_ID_HEADER: Final[str] = "X-Trace-Id"

__all__ = ["REQUEST_ID_HEADER", "TRACE_ID_HEADER"]
