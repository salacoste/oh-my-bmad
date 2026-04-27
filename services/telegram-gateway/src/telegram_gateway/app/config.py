"""TelegramSettings — first service-side AuditedBaseSettings consumer (Story 3.1).

Declares the Telegram bot token + webhook secret token (both wrapped via
:func:`secret_hygiene.audited_secret_field` so every read fires a typed
``secret.accessed`` audit event — Story 2.16 / FR42 / NFR-S3) plus the
webhook public URL, the webhook FastAPI mount path, and the event-log
directory used by the audit writer.

FAIL-CLOSED behavior
--------------------

:py:meth:`TelegramSettings.from_env` resolves the env-vars via
``pydantic-settings`` and raises :class:`pydantic.ValidationError` if any
required variable (``TELEGRAM_BOT_TOKEN``, ``TELEGRAM_WEBHOOK_SECRET_TOKEN``,
``TELEGRAM_WEBHOOK_URL``) is unset / empty. The webhook URL is additionally
validated as ``HttpUrl`` and rejected unless it carries the ``https``
scheme (architecture.md:217 — "Telegram webhook needs HTTPS").

Test fixture convention
-----------------------

Co-located tests under :mod:`telegram_gateway.test_*` use the literal
string ``"fake-bot-token-1234"`` (and similar 4-digit suffixes) so the
``secret-hygiene-precommit`` Telegram bot-token regex
``\\d+:[A-Za-z0-9_-]{35}`` (see
:mod:`secret_hygiene.scanner`) never matches the fixture. Do NOT shorten
the comment to a real-shaped token by accident — keep the digit suffix
short and the colon absent.

Decision: ``event_log_dir`` is declared HERE rather than threaded through
``build_app`` separately. The story spec's AC-4 lifespan code fence
references ``settings.event_log_dir`` so the field has to live on
:class:`TelegramSettings`. Default
``/var/lib/oh-my-bmad/events`` matches the registry-state convention
(:mod:`registry_state.adapters.event_log`'s production deployment path).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import SettingsConfigDict
from secret_hygiene import (
    AuditedBaseSettings,
    AuditedSecret,
    audited_secret_field,
)


class TelegramSettings(AuditedBaseSettings):
    """Telegram bot configuration sourced from env-vars.

    Construct via :py:meth:`AuditedBaseSettings.from_env` (NOT bare
    ``cls()`` — bare construction triggers the
    :data:`secret_hygiene.audited_secret._UNCONFIGURED_ACTOR` warning
    and silently disables emission per Story 2.16 H5).
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        arbitrary_types_allowed=True,
        # Inherit ``extra="ignore"`` from BaseSettings so unrelated env
        # vars don't break instantiation in the shared-process context
        # (.env is read once by the operator's stack).
    )

    bot_token: AuditedSecret = audited_secret_field(
        "telegram_bot_token", env_var="TELEGRAM_BOT_TOKEN"
    )
    webhook_secret_token: AuditedSecret = audited_secret_field(
        "telegram_webhook_secret_token", env_var="TELEGRAM_WEBHOOK_SECRET_TOKEN"
    )
    webhook_url: HttpUrl = Field(validation_alias="TELEGRAM_WEBHOOK_URL")
    webhook_path: str = Field(default="/v1/telegram/webhook")
    # Default mirrors the registry-state production path (Story 2.4) but
    # is overridable for tests + alternate deployment layouts.
    event_log_dir: Path = Field(
        default=Path("/var/lib/oh-my-bmad/events"),
        validation_alias="EVENT_LOG_DIR",
    )

    @field_validator("webhook_url")
    @classmethod
    def _enforce_https(cls, url: HttpUrl) -> HttpUrl:
        """Reject ``http://`` (architecture.md:217 — Telegram requires HTTPS).

        ``pydantic.HttpUrl`` accepts BOTH ``http`` and ``https``; this
        validator narrows the contract to ``https`` only. Failing here
        rather than at ``set_webhook`` time means an operator typo
        surfaces at startup instead of as a Telegram-side 400.
        """
        if url.scheme != "https":
            raise ValueError("webhook_url must be https")
        return url


__all__ = ["TelegramSettings"]
