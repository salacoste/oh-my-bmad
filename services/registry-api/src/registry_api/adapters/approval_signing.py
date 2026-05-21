"""Story 11.1 compute_approval_hmac — relocated to packages/events (Story 11.4 PP3).

This module remains as a re-export for backward compatibility with existing
call sites (``routes/decisions.py``); new callers should import from
``events`` directly. The relocation closes a P0 finding from Story 11.4
pass-1 review: ``scripts/verify_approval.py``'s
``from registry_api.adapters.approval_signing import …`` transitively
triggered ``registry_api/__init__.py``'s ``build_app`` import — pulling
FastAPI, SQLAlchemy, Anthropic and the full registry-state SQL stack into
the "offline" verifier process. That falsified the verifier's
"pure-Python / no FastAPI / no SQLAlchemy" contract.

Pure HMAC logic now lives at
``packages/events/src/events/approval_signing.py``; see that module's
docstring for the canonical-signing-string contract, P1-H1 pipe-injection
guard, and Story 11.4 PP2 ms-precision truncation.
"""

from __future__ import annotations

from events.approval_signing import compute_approval_hmac  # re-export

__all__ = ["compute_approval_hmac"]
