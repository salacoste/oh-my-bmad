# Fixture: cross-service import suppressed via multi-tag noqa — CLEAN.
#
# Story 3.8 review H9: a single line carrying ``# noqa: PLC0415, IMP001 — reason``
# must suppress IMP001. Mirrors the Story 5.4 worker-wrapper line shape where
# a single import needs both PLC0415 (lazy/late import) and IMP001 (cross-service)
# suppressed on the same physical line.
# Owner classification: ("service", "registry-api") — see _meta.py
from registry_state import __version__  # noqa: PLC0415, IMP001 — fixture: multi-tag suppression path

__all__ = ["__version__"]
