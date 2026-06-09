"""Canonical exceptions for the ``mtls`` package (Phase 11 / ADR-0023).

Placed in a dedicated module to break the circular import between
:mod:`mtls.mtls` and :mod:`mtls.certs`.
"""

from __future__ import annotations


class MTLSConfigError(Exception):
    """Raised when mTLS configuration is invalid or incomplete."""
