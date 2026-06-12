"""Certificate validation utilities for mTLS (Phase 11 / ADR-0023).

Provides file-existence and PEM-format checks, expiry warnings, and a
convenience resolver that validates the full cert / key / CA triple.

Design notes
------------
* **stdlib first** — uses ``ssl.PEM_cert_to_DER_cert`` for PEM validation
  and ``cryptography`` (already a transitive dep via PyJWT) for expiry
  parsing.  No new external dependencies.
* **structlog** — warnings are emitted through ``structlog`` so they
  integrate with the project-wide structured logging pipeline.
"""

from __future__ import annotations

import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from cryptography import x509

from mtls._exceptions import MTLSConfigError
from mtls.settings import MTLSSettings

logger = structlog.get_logger(__name__)


def validate_cert_file(path: str, label: str) -> None:
    """Verify that *path* points to a readable, valid PEM certificate.

    Parameters
    ----------
    path:
        Filesystem path to the certificate file.
    label:
        Human-readable label for error messages (e.g. ``"client cert"``).

    Raises
    ------
    MTLSConfigError
        If the file does not exist, is unreadable, or is not valid PEM.
    """
    p = Path(path)
    if not p.is_file():
        raise MTLSConfigError(f"{label}: file not found: {path}")
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise MTLSConfigError(f"{label}: cannot read file: {path} ({exc})") from exc

    if not ssl.PEM_cert_to_DER_cert(raw.decode("ascii", errors="replace").strip()):
        raise MTLSConfigError(f"{label}: not a valid PEM certificate: {path}")


def check_cert_expiry(cert_path: str, warning_hours: int = 24) -> None:
    """Check certificate expiry and warn or raise as appropriate.

    Parameters
    ----------
    cert_path:
        Path to a PEM-encoded certificate.
    warning_hours:
        Emit a structured warning if the cert expires within this many
        hours.  Defaults to 24 (P11-I3 short-lived cert invariant).

    Raises
    ------
    MTLSConfigError
        If the certificate has already expired.
    """
    raw = Path(cert_path).read_bytes()
    cert = x509.load_pem_x509_certificate(raw)
    not_after = cert.not_valid_after_utc
    now = datetime.now(UTC)

    if now >= not_after:
        raise MTLSConfigError(
            f"Certificate has expired: {cert_path} (not-after={not_after.isoformat()})"
        )

    remaining = not_after - now
    if remaining <= timedelta(hours=warning_hours):
        logger.warning(
            "mTLS certificate expiring soon",
            cert_path=cert_path,
            not_after=not_after.isoformat(),
            remaining_hours=remaining.total_seconds() / 3600,
            warning_threshold_hours=warning_hours,
        )


def resolve_cert_paths(settings: MTLSSettings) -> tuple[str, str, str]:
    """Validate and return the cert / key / CA path triple from *settings*.

    Parameters
    ----------
    settings:
        Already-validated mTLS settings (``enabled=True`` implied by caller).

    Returns
    -------
    tuple[str, str, str]
        ``(cert_path, key_path, ca_path)`` — guaranteed to be non-empty
        strings pointing to readable files.

    Raises
    ------
    MTLSConfigError
        If any path is missing, unreadable, or not valid PEM.
    """
    assert settings.cert_path is not None  # guaranteed by model validator
    assert settings.key_path is not None
    assert settings.ca_path is not None

    cert = settings.cert_path
    key = settings.key_path
    ca = settings.ca_path

    validate_cert_file(cert, label="mTLS certificate")
    validate_cert_file(ca, label="mTLS CA bundle")

    # Validate key file exists and is readable (PEM key, not a cert)
    key_p = Path(key)
    if not key_p.is_file():
        raise MTLSConfigError(f"mTLS private key: file not found: {key}")
    try:
        key_p.read_bytes()
    except OSError as exc:
        raise MTLSConfigError(f"mTLS private key: cannot read file: {key} ({exc})") from exc

    check_cert_expiry(cert, warning_hours=settings.rotation_warning_hours)

    logger.debug("mTLS cert paths resolved", cert_path=cert, key_path=key, ca_path=ca)
    return cert, key, ca
