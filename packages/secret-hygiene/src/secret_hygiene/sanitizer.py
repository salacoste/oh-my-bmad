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

Both strategies recurse into nested ``dict``, ``list``, ``tuple``, ``set``,
``frozenset``, and ``bytes`` structures, preserving the original container
types.  A depth guard prevents runaway recursion on circular references.
"""

from __future__ import annotations

import warnings
from collections.abc import MutableMapping
from typing import Any

from .scanner import scan_text

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

REDACTED_SENTINEL: str = "***REDACTED***"

# Maximum recursion depth before the cycle/depth guard fires.
_MAX_DEPTH: int = 20

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
        # Additional sensitive key names (Fix G):
        "x-api-key",
        "client_secret",
        "refresh_token",
        "session_token",
        "cookie",
        "set-cookie",
        "passwd",
        "pwd",
        "credentials",
        "secret_key",
    }
)

# Suffix-based key matching: any key whose casefolded form ends with one of
# these suffixes is treated as sensitive (catches user_api_key, github_token, etc.).
_KEY_REDACT_SUFFIXES: tuple[str, ...] = (
    "_token",
    "_key",
    "_secret",
    "_password",
    "_credential",
    "_credentials",
)


def _is_secret_key(key: str) -> bool:
    """Return ``True`` if *key* (string) names a sensitive field."""
    folded = key.casefold()
    return folded in _KEY_REDACT_SET or any(folded.endswith(s) for s in _KEY_REDACT_SUFFIXES)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _redact_value(value: Any, _depth: int = 0) -> Any:
    """Return *value* with any secrets replaced by :data:`REDACTED_SENTINEL`.

    Handles ``str``, ``bytes``, ``dict`` / ``MutableMapping``, ``list``,
    ``tuple``, ``set``, ``frozenset`` recursively.  Other types pass through
    unchanged.  A depth guard at :data:`_MAX_DEPTH` prevents infinite recursion
    on circular references.
    """
    if _depth > _MAX_DEPTH:
        warnings.warn(
            "secret-hygiene: redact_value depth limit reached; skipping deeper recursion",
            stacklevel=2,
        )
        return REDACTED_SENTINEL

    if isinstance(value, str):
        if scan_text(value):
            return REDACTED_SENTINEL
        return value

    if isinstance(value, bytes):
        # Decode, scan the text, and return the sentinel if any pattern fires.
        # If the bytes are not decodable (rare but possible), skip silently.
        try:
            decoded = value.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return value
        if scan_text(decoded):
            return REDACTED_SENTINEL
        return value

    if isinstance(value, dict):
        return _redact_dict(value, _depth=_depth)

    # Handle non-dict MutableMappings (e.g. ChainMap, UserDict).
    if isinstance(value, MutableMapping):
        result: dict[Any, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _is_secret_key(k):
                result[k] = REDACTED_SENTINEL
            else:
                result[k] = _redact_value(v, _depth=_depth + 1)
        return result

    if isinstance(value, list):
        return [_redact_value(item, _depth=_depth + 1) for item in value]

    if isinstance(value, tuple):
        return tuple(_redact_value(item, _depth=_depth + 1) for item in value)

    if isinstance(value, frozenset):
        return frozenset(
            REDACTED_SENTINEL if isinstance(item, str) and scan_text(item) else item
            for item in value
        )

    if isinstance(value, set):
        return {
            REDACTED_SENTINEL if isinstance(item, str) and scan_text(item) else item
            for item in value
        }

    return value


def _redact_dict(d: dict[Any, Any], _depth: int = 0) -> dict[Any, Any]:
    """Redact all sensitive keys and values in *d* (in-place + returned).

    Non-string keys are never tested for name-based redaction (they have no
    ``.casefold()``), but their associated values are still scanned recursively.
    """
    for key in list(d.keys()):
        value = d[key]
        if isinstance(key, str) and _is_secret_key(key):
            d[key] = REDACTED_SENTINEL
        else:
            d[key] = _redact_value(value, _depth=_depth + 1)
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
