"""Defense-in-depth: ``repr(TelegramSettings)`` must not leak plaintext (Story 2.16 H1).

AC-10 explicitly mandates this test even though
:py:meth:`secret_hygiene.AuditedSecret.__repr__` already returns
``"<REDACTED:secret_name>"`` and :class:`AuditedBaseSettings` overrides
``__repr__`` to format every field via ``repr()``. The test pins the
contract against future regressions of either layer.
"""

from __future__ import annotations

import pytest
from events.envelope import Actor

from telegram_gateway.app.config import TelegramSettings

_ACTOR = Actor(kind="system", id="telegram-gateway")


def test_repr_does_not_leak_plaintext_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:fake-bot-token")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "fake-webhook-secret-9999")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://tunnel.example.com/v1")

    settings = TelegramSettings.from_env(emit=None, actor=_ACTOR)
    rendered = repr(settings)

    assert "1234:fake-bot-token" not in rendered
    assert "fake-webhook-secret-9999" not in rendered
    # Both redacted forms should be present (defense-in-depth assertion).
    assert "<REDACTED:telegram_bot_token>" in rendered
    assert "<REDACTED:telegram_webhook_secret_token>" in rendered
