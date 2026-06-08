"""Log-sanitizer integration test for telegram-gateway (Story 3.6 AC-8/10).

Two layers of coverage (Story 3.6 H1 review fix):
  - ``test_redact_secrets_redacts_telegram_bot_token_key_unit`` — pure-unit
    pin on key-name redaction (``"telegram_bot_token"`` is in
    ``_KEY_REDACT_SET``). Kept as a fast regression smoke test for the
    key-name code path.
  - ``test_log_sanitizer_redacts_bot_token_in_telegram_gateway_logs`` —
    integration test that exercises the AC-8 spec path: a stdlib log call
    carrying a non-redact-set key (``received``) with a value-pattern-matching
    Telegram bot token, captured through the configured ``ProcessorFormatter``
    chain, asserting the secret is replaced by ``***REDACTED***`` in the
    rendered JSON. Production code never logs the token; this pins the
    SAFETY NET so a future bug cannot accidentally leak the token through
    structured logs.
"""

from __future__ import annotations


def test_redact_secrets_redacts_telegram_bot_token_key_unit() -> None:
    """Pure unit: ``redact_secrets`` redacts values stored under the bot-token key.

    The key name ``"telegram_bot_token"`` is in ``_KEY_REDACT_SET`` so any
    value stored there is replaced by ``REDACTED_SENTINEL`` regardless of
    its content / entropy. Calls the sanitizer processor directly with a
    synthetic event_dict — does NOT exercise the full structlog chain
    (covered by the integration sibling test below).
    """
    from secret_hygiene.sanitizer import REDACTED_SENTINEL, redact_secrets  # noqa: PLC0415

    event_dict: dict[str, object] = {
        "event": "startup",
        "telegram_bot_token": "***FAKE-BOT-TOKEN-DOES-NOT-MATTER***",
        "level": "info",
    }

    result = redact_secrets(None, None, event_dict)

    # The key name ``telegram_bot_token`` is in ``_KEY_REDACT_SET`` — the
    # value is unconditionally replaced regardless of its content.
    assert result["telegram_bot_token"] == REDACTED_SENTINEL, (
        f"Bot token value was NOT redacted; got: {result['telegram_bot_token']!r}"
    )
    # Event message itself is untouched.
    assert result["event"] == "startup"


def test_log_sanitizer_redacts_bot_token_in_telegram_gateway_logs() -> None:
    """AC-8 integration: value-pattern redaction fires in the configured chain.

    Story 3.6 H1: configures the AC-4 chain via
    :func:`telegram_gateway.__main__._configure_logging`, captures the
    rendered JSON output, and asserts a Telegram-bot-token-shaped value
    written under a NON-redact-set key (``received``, the field
    ``IdempotencyKeyMiddleware`` would use in registry-api; in
    telegram-gateway this pins the safety net for any hypothetical future
    log path) is replaced by ``***REDACTED***`` in the output. The bot
    token literal MUST NOT appear in the captured rendered JSON.
    """
    import io  # noqa: PLC0415
    import logging  # noqa: PLC0415

    from telegram_gateway.__main__ import _configure_logging  # noqa: PLC0415

    # Build a TELEGRAM_BOT_TOKEN-shaped value WITHOUT embedding a single
    # contiguous secret-shape literal in source (so the precommit
    # secret-hygiene scanner does not flag this test file). The runtime
    # concatenation produces ``123456:AA<32 hex chars>`` which matches the
    # TELEGRAM_BOT_TOKEN regex in ``secret_hygiene.scanner``.
    bot_token_value = "123456:" + "AA" + ("0123456789abcdef" * 2)

    # Configure the canonical AC-4 chain (idempotent — the
    # ``_STRUCTLOG_CONFIGURED`` sentinel makes re-entry a no-op).
    _configure_logging()

    # Re-point the root handler at an in-memory buffer so we can read the
    # rendered JSON. Restore in ``finally`` so other tests are not polluted.
    buf = io.StringIO()
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    existing_formatter = original_handlers[0].formatter if original_handlers else None
    capture_handler = logging.StreamHandler(buf)
    if existing_formatter is not None:
        capture_handler.setFormatter(existing_formatter)
    root.handlers = [capture_handler]
    root.setLevel(logging.DEBUG)

    try:
        # Simulate a hypothetical leak: stdlib log call with the secret in
        # the ``received`` key (NOT in ``_KEY_REDACT_SET`` — value-pattern
        # redaction is the safety net here).
        leak_logger = logging.getLogger("telegram_gateway.test_safety_net")
        leak_logger.warning(
            "synthetic-leak: hypothetical webhook decode error",
            extra={"received": bot_token_value},
        )
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)

    captured = buf.getvalue()
    assert "***REDACTED***" in captured, (
        f"sanitizer did not fire on the rendered chain; output:\n{captured}"
    )
    assert bot_token_value not in captured, (
        f"bot token literal leaked in rendered JSON output:\n{captured}"
    )
