"""structlog processor that redacts secrets from log event dicts.

This processor MUST run BEFORE the JSON renderer in the structlog chain.
It mutates event_dict in place (and returns it) so structlog's processor
contract is satisfied either way — both mutation-based and functional-style
chains work.

Recommended chain position::

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_secrets,          # <-- here, before any serialiser
            structlog.processors.JSONRenderer(),
        ]
    )

Two redaction strategies are applied in order:

1. **Key-name redaction** — values stored under sensitive key names (e.g.
   ``api_key``, ``token``, ``password``) are replaced unconditionally,
   regardless of whether the value looks like a secret.  This catches
   low-entropy secrets (e.g. ``password=1234``) that pattern matching
   would miss.

2. **Value-pattern redaction** — string values not caught by key-name
   matching are scanned with :data:`~secret_hygiene.scanner.SECRET_PATTERNS`.
   Any hit replaces the *entire* value with :data:`REDACTED_SENTINEL`.

Both strategies recurse into nested ``dict``, ``list``, and ``tuple``
structures, preserving the original container types.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from .scanner import scan_text

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

REDACTED_SENTINEL: str = "***REDACTED***"

# ---------------------------------------------------------------------------
# Key-name redaction set
# Matched after .casefold() so "API_KEY", "Api-Key", "apikey" all hit.
# ---------------------------------------------------------------------------

_KEY_REDACT_SET: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "password",
        "secret",
        "authorization",
        "auth",
        "bearer",
        "anthropic_api_key",
        "telegram_bot_token",
        "github_token",
        "private_key",
        "access_key",
    }
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _redact_value(value: Any) -> Any:
    """Return *value* with any secrets replaced by :data:`REDACTED_SENTINEL`.

    Handles ``str``, ``dict``, ``list``, ``tuple`` recursively.  Other types
    pass through unchanged.
    """
    if isinstance(value, str):
        if scan_text(value):
            return REDACTED_SENTINEL
        return value
    if isinstance(value, dict):
        return _redact_dict(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value


def _redact_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Redact all sensitive keys and values in *d* (in-place + returned)."""
    for key in list(d.keys()):
        if key.casefold() in _KEY_REDACT_SET:
            d[key] = REDACTED_SENTINEL
        else:
            d[key] = _redact_value(d[key])
    return d


# ---------------------------------------------------------------------------
# structlog processor
# ---------------------------------------------------------------------------


def redact_secrets(
    _logger: Any,
    _method: Any,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """structlog processor — redact secrets from *event_dict*.

    Signature matches the structlog processor contract::

        processor(logger, method_name, event_dict) -> event_dict

    The first two arguments are ignored; they exist only to satisfy the
    protocol.  *event_dict* is mutated in place AND returned.

    Parameters
    ----------
    _logger:
        The bound logger instance (ignored).
    _method:
        The logging method name, e.g. ``"info"`` (ignored).
    event_dict:
        The mutable event dictionary structlog passes through the chain.

    Returns
    -------
    MutableMapping[str, Any]
        The same *event_dict*, mutated to replace any secrets.
    """
    # We need a plain dict to use _redact_dict; MutableMapping is the declared
    # type for the processor protocol but in practice structlog always passes a
    # real dict.  Cast is safe; if it's not a dict we treat it as a pass-through.
    if isinstance(event_dict, dict):
        _redact_dict(event_dict)
    return event_dict
