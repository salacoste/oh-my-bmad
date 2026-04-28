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
string ``"1234:fake-bot-token"`` (and similar 4-digit-prefix shapes) so the
``secret-hygiene-precommit`` Telegram bot-token regex
``\\d{6,12}:AA[A-Za-z0-9_\\-]{30,}`` (see
:mod:`secret_hygiene.scanner`) never matches the fixture. Do NOT shorten
the comment to a real-shaped token by accident — keep the digit prefix
short (4 digits, never 6+) and never include the ``AA`` suffix marker.

Decision: ``event_log_dir`` is declared HERE rather than threaded through
``build_app`` separately. The story spec's AC-4 lifespan code fence
references ``settings.event_log_dir`` so the field has to live on
:class:`TelegramSettings`. Default
``/var/lib/oh-my-bmad/events`` matches the registry-state convention
(:mod:`registry_state.adapters.event_log`'s production deployment path).
"""

from __future__ import annotations

import contextlib
import ipaddress
import string
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

from pydantic import Field, HttpUrl, field_validator, model_validator
from pydantic_settings import SettingsConfigDict
from secret_hygiene import (
    AuditedBaseSettings,
    AuditedSecret,
    audited_secret_field,
)

# Hostnames + IP networks rejected by ``_enforce_https`` because Telegram
# itself refuses to deliver webhooks to private/loopback/link-local
# targets. Failing at config-load time produces a clear operator error
# rather than a silent no-deliver downstream (review-fix M13).
_REJECTED_HOSTNAMES: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

# ASCII-printable charset accepted for ``webhook_secret_token`` (review-fix
# L8). Telegram's docs state the secret token may contain only ASCII
# characters; we narrow further to printable (no control chars, no
# whitespace) to catch operator copy-paste mishaps at config-load.
_ASCII_PRINTABLE: frozenset[str] = frozenset(
    string.ascii_letters + string.digits + string.punctuation
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
    # Review-fix M18: ``webhook_path`` is overridable via env. Review-fix
    # H1/L13: leading slash required, trailing slash rejected (unless the
    # value is exactly ``/``). Combined with ``main.build_app`` mounting
    # the route from this value, the operator-set path takes effect
    # instead of being silently ignored.
    webhook_path: str = Field(
        default="/v1/telegram/webhook",
        validation_alias="TELEGRAM_WEBHOOK_PATH",
    )
    # Default mirrors the registry-state production path (Story 2.4) but
    # is overridable for tests + alternate deployment layouts.
    event_log_dir: Path = Field(
        default=Path("/var/lib/oh-my-bmad/events"),
        validation_alias="EVENT_LOG_DIR",
    )
    # Story 3.2 / FR11 / NFR-S4: allowlist of Telegram user ids.
    # Closed-by-default — empty frozenset rejects every inbound update
    # (including the operator's own id). The lifespan emits a startup
    # WARNING when this set is empty so the operator notices their
    # ``.env`` is incomplete on first boot. ``pydantic-settings`` parses
    # JSON-list syntax (``[12345, 67890]``) natively for ``frozenset[int]``.
    tg_allowlist_user_ids: frozenset[int] = Field(
        default_factory=frozenset,
        validation_alias="TG_ALLOWLIST_USER_IDS",
        description=(
            "JSON list of allowed Telegram user ids. Empty default = "
            "closed-by-default (rejects every inbound update). FR11."
        ),
    )

    @field_validator("tg_allowlist_user_ids")
    @classmethod
    def _validate_allowlist_positive(cls, ids: frozenset[int]) -> frozenset[int]:
        """Reject Telegram user ids ``<= 0`` (Story 3.2 AC-2).

        Real Telegram user ids are positive integers; ``0`` and negative
        values are not real ids. The ``user_id=0`` sentinel used by the
        ``telegram.rejected`` payload's ``no_from_user`` branch is set
        by the middleware itself, not by operator config.
        """
        bad = [i for i in ids if i <= 0]
        if bad:
            raise ValueError(
                f"tg_allowlist_user_ids must contain positive integers; "
                f"got non-positive value(s): {sorted(bad)!r}"
            )
        return ids

    @field_validator("webhook_path")
    @classmethod
    def _validate_webhook_path(cls, value: str) -> str:
        """Require a leading slash; reject trailing slash unless ``/`` (review-fix H1/L13)."""
        if not value.startswith("/"):
            raise ValueError(f"webhook_path must start with '/': got {value!r}")
        if value != "/" and value.endswith("/"):
            raise ValueError(
                f"webhook_path must not end with '/' (got {value!r}); "
                "a trailing slash creates a route-mismatch with the URL "
                "Telegram echoes back"
            )
        return value

    @field_validator("webhook_url")
    @classmethod
    def _enforce_https(cls, url: HttpUrl) -> HttpUrl:
        """Reject ``http://``, userinfo, private/loopback hosts.

        - HTTPS-only (architecture.md:217 — Telegram requires HTTPS).
        - No userinfo (review-fix L10) — defense-in-depth against credential
          leakage in logs / process tables.
        - No private/loopback/link-local hosts (review-fix M13) — Telegram
          will not deliver to RFC 1918 / 169.254 / loopback addresses, so
          fail at startup rather than silently no-deliver.
        """
        if url.scheme != "https":
            raise ValueError("webhook_url must be https")
        if url.username or url.password:
            raise ValueError("webhook_url must not contain userinfo (user:pass@)")
        host = url.host or ""
        if host.lower() in _REJECTED_HOSTNAMES:
            raise ValueError(
                f"webhook_url host {host!r} is loopback/wildcard; Telegram cannot deliver to it"
            )
        try:
            parsed_ip = ipaddress.ip_address(host)
        except ValueError:
            # Hostname (DNS) — accept; private-network IPs are caught above.
            pass
        else:
            if (
                parsed_ip.is_private
                or parsed_ip.is_loopback
                or parsed_ip.is_link_local
                or parsed_ip.is_reserved
                or parsed_ip.is_multicast
            ):
                raise ValueError(
                    f"webhook_url host {host!r} is private/loopback/"
                    "link-local; Telegram cannot deliver to it"
                )
        return url

    @field_validator("webhook_secret_token")
    @classmethod
    def _validate_webhook_secret_charset(cls, secret: AuditedSecret) -> AuditedSecret:
        """Require ASCII-printable charset (review-fix L8).

        Telegram echoes the secret back in the
        ``X-Telegram-Bot-Api-Secret-Token`` HTTP header; non-ASCII
        characters cannot be transmitted reliably. Catch operator
        copy-paste mishaps (smart quotes, NBSP, etc.) at config-load.
        """
        # Reading ``.value`` on a placeholder-wrapped AuditedSecret is
        # safe (emit=None) — no audit envelope is fired.
        value = secret.value
        if not value:
            raise ValueError("webhook_secret_token must be non-empty")
        for ch in value:
            if ch not in _ASCII_PRINTABLE:
                raise ValueError(
                    "webhook_secret_token must contain only ASCII-printable "
                    f"characters (offending char: {ch!r})"
                )
        return secret

    @model_validator(mode="after")
    def _validate_url_path_matches_route(self) -> TelegramSettings:
        """Pin ``webhook_url.path`` ≡ ``webhook_path`` (review-fix H2).

        A typo on the operator's tunnel config (e.g.,
        ``TELEGRAM_WEBHOOK_URL=https://tunnel/v2/telegram/webhook`` while
        ``webhook_path`` defaults to ``/v1/telegram/webhook``) would let
        ``set_webhook`` succeed silently — Telegram would then deliver to
        a 404 forever and the cold-start audit count silently drops.

        Use :func:`urllib.parse.urlparse` directly instead of pydantic's
        ``url.path`` because pydantic auto-appends ``/`` to bare-host
        URLs and we want the operator-supplied form preserved.
        """
        url_path = urlparse(str(self.webhook_url)).path
        if url_path != self.webhook_path:
            raise ValueError(
                f"webhook_url path must match webhook_path; "
                f"got {url_path!r} vs {self.webhook_path!r}"
            )
        return self

    @model_validator(mode="after")
    def _probe_event_log_dir_writable(self) -> TelegramSettings:
        """Probe-then-delete a marker file under ``event_log_dir`` (review-fix M12).

        A read-only volume mount (``:ro`` in compose) would silently drop
        every audit event without surfacing as an error — :class:`EventLogWriter`
        catches OSError on append + logs a warning per event but the
        operator may never see the warning storm. Probe at config-load
        instead: try to create the directory and write+delete a marker
        file. Surfaces config errors at boot, fail-closed.
        """
        target = Path(self.event_log_dir)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"event_log_dir {target!s} cannot be created: {exc}") from exc
        probe = target / ".write-probe"
        try:
            probe.write_text("x", encoding="ascii")
        except OSError as exc:
            raise ValueError(f"event_log_dir {target!s} is not writable: {exc}") from exc
        finally:
            # Best-effort cleanup; ignore if the write itself failed.
            with contextlib.suppress(OSError):
                probe.unlink()
        return self


__all__ = ["TelegramSettings"]
