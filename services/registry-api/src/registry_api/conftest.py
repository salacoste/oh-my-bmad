"""Pytest configuration for the ``services/registry-api`` package.

Story 11.5 PP2 — scrub ``OPERATOR_HMAC_KEY`` from process environment
before each test runs. Pre-PP2, the env-var leaked from CI/dev shells
into tests that build ``build_app(...)`` without explicitly passing
``signing_settings``: the lifespan rotation detector would observe a
real key, emit a first-boot ``key.rotated`` event, and skew event-count
assertions in ``test_approvals.py``, ``test_app.py``, ``test_events.py``,
etc.

Tests that NEED a signing key inject it explicitly via
``ApprovalSigningSettings(operator_hmac_key=...)`` at ``build_app()``
time (e.g. ``test_decisions_signing.py``, ``tests/integration/
test_hmac_key_isolation.py``). Those tests are unaffected by this
fixture since the signing settings parameter takes precedence over
the env-var.

The fixture is autouse + module-scope-agnostic — it applies to every
test in the registry-api package. The ``monkeypatch.delenv(...,
raising=False)`` form is idempotent: a missing env-var is a no-op.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _scrub_operator_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Story 11.5 PP2 — scrub OPERATOR_HMAC_KEY from process env.

    Tests that don't explicitly construct ``ApprovalSigningSettings``
    must not see a stray rotation-detector emission from a leaked
    env-var. Tests that need a key inject it via
    ``ApprovalSigningSettings(operator_hmac_key=...)`` at
    ``build_app()`` time, which takes precedence over the env-var.
    """
    monkeypatch.delenv("OPERATOR_HMAC_KEY", raising=False)
