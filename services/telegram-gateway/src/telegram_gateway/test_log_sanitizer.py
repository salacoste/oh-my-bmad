"""Log-sanitizer integration test for telegram-gateway (Story 3.6 AC-8/10).

1 test:
  - test_log_sanitizer_redacts_bot_token_in_telegram_gateway_logs

Verifies that ``secret_hygiene.sanitizer.redact_secrets`` fires on log
records containing a Telegram bot token (``<int>:<str>`` pattern, which
matches the platform's secret-pattern scanner). The test uses a synthetic
log call — production code never logs the token; this pins the SAFETY NET
so a future bug cannot accidentally leak the token through structured logs.
"""

from __future__ import annotations


def test_log_sanitizer_redacts_bot_token_in_telegram_gateway_logs() -> None:
    """A log record with a Telegram bot token value is redacted by redact_secrets.

    The Telegram bot token format is ``<numeric_id>:<alphanumeric_string>``.
    ``secret_hygiene.scanner.SECRET_PATTERNS`` includes a regex that matches
    this format so the sanitizer replaces the value with ``***REDACTED***``.

    Technique: call the structlog processor directly with a synthetic
    event_dict. This avoids needing the full structlog chain wired (which only
    happens in __main__.py at production runtime) while still exercising the
    exact code path that protects telegram-gateway logs.
    """
    from secret_hygiene.sanitizer import REDACTED_SENTINEL, redact_secrets  # noqa: PLC0415

    # Simulate a hypothetical log call that accidentally includes the token.
    # In production the token is always wrapped in AuditedSecret and never
    # passed as a plain string — this test pins the fallback safety net.
    event_dict: dict[str, object] = {
        "event": "startup",
        "telegram_bot_token": "1234:fake-bot-token-abc",
        "level": "info",
    }

    result = redact_secrets(None, None, event_dict)  # type: ignore[arg-type]

    # The key name "telegram_bot_token" contains "token" → key-name redaction.
    assert result["telegram_bot_token"] == REDACTED_SENTINEL, (
        f"Bot token was NOT redacted; got: {result['telegram_bot_token']!r}"
    )
    # Event message itself is untouched.
    assert result["event"] == "startup"
