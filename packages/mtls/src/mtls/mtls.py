"""Core mTLS SSL context factory (Phase 11 / ADR-0023).

Provides helpers that create ``ssl.SSLContext`` instances for server-side
and client-side mTLS, reading certificate paths from environment variables
via :mod:`mtls.settings`.  The canonical ``MTLSConfigError`` exception
lives in :mod:`mtls._exceptions`.

Design notes
------------
* **All-or-nothing (P11-I1).**  When ``MTLS_ENABLED`` is true the caller
  must supply valid cert / key / CA paths; partial config is a hard error.
* **Short-lived certs (P11-I3).**  The factory checks expiry at creation
  time and warns via ``structlog`` when a cert expires within 24 hours.
* **Graceful degradation.**  When mTLS is disabled (``MTLS_ENABLED``
  unset / falsy), helpers return ``None`` / ``True`` so callers fall back
  to plain HTTP — identical to Phase 10 behaviour (NFR-M11).
"""

from __future__ import annotations

import ssl
from typing import Literal, TypedDict

import structlog
from cryptography import x509

from mtls.certs import check_cert_expiry, resolve_cert_paths
from mtls.settings import MTLSSettings

logger = structlog.get_logger(__name__)


class UvicornSSLConfig(TypedDict):
    """Typed subset of uvicorn TLS keyword arguments used by registry-api."""

    ssl_keyfile: str
    ssl_certfile: str
    ssl_ca_certs: str
    ssl_cert_reqs: int


def _cert_cn(cert_path: str) -> str:
    """Extract the RFC 4514 subject string from a PEM certificate."""
    from pathlib import Path

    raw = Path(cert_path).read_bytes()
    cert = x509.load_pem_x509_certificate(raw)
    return cert.subject.rfc4514_string()


def _cert_not_after(cert_path: str) -> str:
    """Return the ``not_valid_after_utc`` ISO-8601 string for logging."""
    from pathlib import Path

    raw = Path(cert_path).read_bytes()
    cert = x509.load_pem_x509_certificate(raw)
    return cert.not_valid_after_utc.isoformat()


def create_ssl_context(role: Literal["server", "client"]) -> ssl.SSLContext | None:
    """Build an ``ssl.SSLContext`` for mTLS from environment settings.

    Reads configuration via :meth:`MTLSSettings.from_env`.  When mTLS is
    not enabled (``MTLS_ENABLED`` unset / falsy), returns ``None`` so the
    caller falls back to plain HTTP — identical to Phase 10 behaviour
    (NFR-M11).

    Parameters
    ----------
    role:
        ``"server"`` for an inbound listener context,
        ``"client"`` for an outbound connection context.

    Returns
    -------
    ssl.SSLContext | None
        A fully-configured TLS 1.2+ context with cert / key / CA loaded,
        or ``None`` when mTLS is disabled.

    Raises
    ------
    MTLSConfigError
        If mTLS is enabled but certificate files are missing, unreadable,
        expired, or otherwise invalid.
    """
    settings = MTLSSettings.from_env()

    if not settings.enabled:
        logger.debug("mTLS disabled, using plain HTTP")
        return None

    cert_path, key_path, ca_path = resolve_cert_paths(settings)
    check_cert_expiry(cert_path, warning_hours=settings.rotation_warning_hours)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.options |= ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1

    ctx.load_verify_locations(cafile=ca_path)
    ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)

    # Server verifies the client certificate but does not match hostnames.
    # Client must verify that the server hostname matches the cert.
    ctx.check_hostname = role == "client"

    cn = _cert_cn(cert_path)
    expiry = _cert_not_after(cert_path)

    logger.info(
        "mTLS context created",
        role=role,
        cert_cn=cn,
        cert_expiry=expiry,
    )
    return ctx


def create_uvicorn_ssl_config() -> UvicornSSLConfig | None:
    """Build the ``ssl`` keyword-argument dict for uvicorn.

    Returns
    -------
    UvicornSSLConfig | None
        A dict suitable for ``uvicorn.Config(app, **ssl=...)`` when mTLS
        is enabled, or ``None`` when mTLS is disabled.

    Raises
    ------
    MTLSConfigError
        If mTLS is enabled but configuration is invalid.
    """
    settings = MTLSSettings.from_env()

    if not settings.enabled:
        return None

    cert_path, key_path, ca_path = resolve_cert_paths(settings)
    check_cert_expiry(cert_path, warning_hours=settings.rotation_warning_hours)

    return {
        "ssl_keyfile": key_path,
        "ssl_certfile": cert_path,
        "ssl_ca_certs": ca_path,
        "ssl_cert_reqs": int(ssl.CERT_REQUIRED),
    }


def create_httpx_verify_arg() -> ssl.SSLContext | bool:
    """Build the ``verify`` argument for ``httpx.AsyncClient``.

    Returns
    -------
    ssl.SSLContext | bool
        A client-side ``SSLContext`` when mTLS is enabled, or ``True``
        (default certificate verification) when mTLS is disabled.

    Raises
    ------
    MTLSConfigError
        If mTLS is enabled but configuration is invalid.
    """
    ctx = create_ssl_context(role="client")
    if ctx is None:
        return True
    return ctx
