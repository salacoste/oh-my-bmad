"""secret-hygiene — Three-layer secret hygiene enforcement per NFR-S1: scanner (pre-commit), structlog log-sanitizer (runtime), license-scan wrapper (pre-push), plus :class:`AuditedSecret` wrapper for FR42 / NFR-S3 audit emission.

Story 1.2 shipped only ``__version__``. Stories 1.7 (scanner + sanitizer) and
6.8–6.10 (pre-commit hook + license scan) added the runtime + tooling layers.
Story 2.16 adds the audit-emission layer via :class:`AuditedSecret` +
:class:`AuditedBaseSettings` + :func:`audited_secret_field`.
"""

from .audited_secret import AuditedBaseSettings, AuditedSecret, audited_secret_field

__version__ = "0.2.0"

__all__ = [
    "AuditedBaseSettings",
    "AuditedSecret",
    "__version__",
    "audited_secret_field",
]
