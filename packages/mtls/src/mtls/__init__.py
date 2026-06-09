"""TLS context factory for mTLS in oh-my-bmad Docker network (Phase 11 / ADR-0023).

Creates ``ssl.SSLContext`` instances for server-side and client-side mTLS,
reading certificate paths from environment variables.

Environment variables
---------------------
MTLS_ENABLED  (OPTIONAL, default ``false``)
    Set to ``true`` / ``1`` / ``yes`` to activate mTLS.  Any other value
    (or unset) means plain HTTP — identical to Phase 10 behaviour (NFR-M11).

MTLS_CERT_PATH  (REQUIRED when MTLS_ENABLED=true)
    Absolute path to the PEM-encoded client/server certificate file.

MTLS_KEY_PATH  (REQUIRED when MTLS_ENABLED=true)
    Absolute path to the PEM-encoded private key file for the certificate.

MTLS_CA_PATH  (REQUIRED when MTLS_ENABLED=true)
    Absolute path to the PEM-encoded CA certificate bundle used to verify
    peer certificates.

Design notes
------------
* **All-or-nothing (P11-I1).** If ``MTLS_ENABLED`` is true, ALL three paths
  must be present and valid.  Partial config raises ``MTLSConfigError`` —
  never silent plaintext fallback.
* **Short-lived certs (P11-I3).** Expiry is checked at context-creation time
  and a warning is logged when a cert expires within 24 hours.  The caller
  can decide whether to proceed.
* **No new external dependencies.** stdlib ``ssl`` + ``cryptography``
  (transitive via PyJWT) are the only crypto primitives.
"""

from __future__ import annotations

from mtls._exceptions import MTLSConfigError
from mtls.mtls import (
    create_httpx_verify_arg,
    create_ssl_context,
    create_uvicorn_ssl_config,
)
from mtls.settings import MTLSSettings

__all__ = [
    "MTLSConfigError",
    "MTLSSettings",
    "create_httpx_verify_arg",
    "create_ssl_context",
    "create_uvicorn_ssl_config",
]
