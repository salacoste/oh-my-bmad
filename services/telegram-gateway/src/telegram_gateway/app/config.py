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
import logging
import os
import string
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

from events import ensure_shared_dir
from pydantic import Field, HttpUrl, field_validator, model_validator
from pydantic_settings import SettingsConfigDict
from secret_hygiene import (
    AuditedBaseSettings,
    AuditedSecret,
    audited_secret_field,
)

_log = logging.getLogger("telegram_gateway.config")


def _coerce_allowlist_raw_string(raw: str, field_name: str = "TG_ALLOWLIST_USER_IDS") -> Any:
    """Normalise raw env-var string for an allowlist field (H3 / M2 / M3; pass-3 UM-3).

    Called from the class-level ``model_validator(mode="before")`` BEFORE
    pydantic-settings attempts JSON parsing, so problematic strings that
    would produce an opaque ``SettingsError`` are handled explicitly:

    * ``""`` (empty string) → ``[]`` (closed-by-default)
    * ``"null"`` / ``"None"`` → ``[]`` with INFO log (M2)
    * JSON array/object (starts with ``[`` or ``{``) → returned unchanged so
      pydantic-settings can JSON-parse it normally
    * bare CSV ``"12345,67890"`` → coerced to ``[12345, 67890]`` + INFO log (M3)

    Pass-3 UM-3: ``field_name`` lets the function be reused for fields other
    than ``TG_ALLOWLIST_USER_IDS`` (e.g. ``TRACE_ALLOWED_CHAT_IDS``) so log
    messages reference the actual env-var the operator set.
    """
    stripped = raw.strip()
    if stripped == "":
        return []
    if stripped.lower() in ("null", "none"):
        _log.info(
            "%s=%r coerced to [] (closed-by-default); set to a JSON list of ids, e.g. [12345]",
            field_name,
            stripped,
        )
        return []
    if stripped.startswith("[") or stripped.startswith("{"):
        # Let pydantic-settings do its normal JSON parse.
        return raw
    # Bare CSV: "12345,67890" — coerce permissively + recommend JSON form.
    parts = [p.strip() for p in stripped.split(",") if p.strip()]
    try:
        coerced = [int(p) for p in parts]
    except ValueError:
        raise ValueError(
            f"{field_name}={raw!r} is not valid JSON and could "
            "not be parsed as comma-separated integers. "
            f"Use JSON list syntax, e.g. {field_name}=[12345,67890]"
        ) from None
    _log.info(
        "%s: bare CSV %r coerced to %r; prefer JSON list form [%s] for clarity",
        field_name,
        stripped,
        coerced,
        ",".join(str(i) for i in coerced),
    )
    return coerced


def _coerce_allowlist_env(value: Any) -> Any:
    """BeforeValidator for ``tg_allowlist_user_ids`` (M1 / L11).

    Runs AFTER pydantic-settings JSON parsing. Handles the post-parse
    form (list, frozenset, set) to reject bool values (M1).

    Note: empty-string / null / CSV normalisation is handled earlier in
    ``TelegramSettings._pre_coerce_allowlist`` (class-level model_validator
    mode="before") which runs before pydantic-settings JSON parsing.
    """
    if isinstance(value, (list, frozenset, set)):
        # Reject bool items (M1): [true] coerces to frozenset({1}) without this.
        bad_bools = [item for item in value if isinstance(item, bool)]
        if bad_bools:
            raise ValueError(
                f"TG_ALLOWLIST_USER_IDS contains boolean value(s) {bad_bools!r}; "
                "use integer user ids, e.g. [12345]"
            )
    return value


# Hostnames + IP networks rejected by ``_enforce_https`` because Telegram
# itself refuses to deliver webhooks to private/loopback/link-local
# targets. Failing at config-load time produces a clear operator error
# rather than a silent no-deliver downstream (review-fix M13).
_REJECTED_HOSTNAMES: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

# Story 11.3.7 / AC2 / D2-A — hermetic test-mode constants.
#
# When ``TELEGRAM_SKIP_WEBHOOK_SET=1`` is set, the lifespan skips the live
# ``bot.set_webhook(...)`` call AND the operator is not required to supply
# ``TELEGRAM_WEBHOOK_URL`` / ``TELEGRAM_WEBHOOK_SECRET_TOKEN``. These
# constants provide placeholder values that satisfy field validators
# (https + non-rejected host + path-matches-webhook-path + ASCII-printable
# non-empty secret) so settings construction succeeds in hermetic CI envs.
# The ``.invalid`` TLD (RFC 2606) is guaranteed-unresolvable, so even if
# code paths leaked these defaults to actual HTTP traffic the request
# would fail-closed at DNS rather than reach a third-party host.
#
# Production behaviour (skip flag unset → falsy) is unchanged: missing
# ``TELEGRAM_WEBHOOK_URL`` / ``TELEGRAM_WEBHOOK_SECRET_TOKEN`` still raise
# ``ValidationError`` fail-closed at boot.
#
# Default webhook path is the same value as :class:`TelegramSettings`'s
# ``webhook_path`` field default — kept in sync intentionally; if the
# default ever changes there, update this constant too (the f-string
# composition in :func:`apply_hermetic_defaults_to_env` then picks up
# whatever ``TELEGRAM_WEBHOOK_PATH`` the operator overrides at runtime).
_HERMETIC_WEBHOOK_URL_HOST: str = "https://hermetic.test.invalid"
_DEFAULT_WEBHOOK_PATH: str = "/v1/telegram/webhook"
HERMETIC_WEBHOOK_SECRET_TOKEN: str = "hermetic-test-secret-skip-mode-no-traffic"
_HERMETIC_SKIP_ENV_VAR: str = "TELEGRAM_SKIP_WEBHOOK_SET"
# Truthy set aligned with pydantic-settings' bool coercion accept-set so
# the helper and the ``telegram_skip_webhook_set: bool`` field never
# disagree on edge inputs like ``"yes"`` / ``"y"`` / ``"t"`` / uppercase.
_HERMETIC_SKIP_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "y", "on", "t"})


def apply_hermetic_defaults_to_env() -> None:
    """When skip-flag is truthy, fill dummy webhook env-vars (idempotent).

    Call BEFORE :py:meth:`TelegramSettings.from_env` so pydantic-settings
    sees the dummy values for ``TELEGRAM_WEBHOOK_URL`` /
    ``TELEGRAM_WEBHOOK_SECRET_TOKEN`` if hermetic skip mode is enabled AND
    the operator hasn't already supplied real values.

    **Empty-string handling** — ``os.environ.setdefault`` only treats
    ABSENCE as "fillable"; an explicitly-empty env-var (e.g. from a future
    docker-compose ``${TELEGRAM_WEBHOOK_URL:-}`` substitution that resolves
    to ``""`` when the shell is unset) would defeat the dummy-fill. We
    treat any empty / whitespace-only value as fillable so the skip-mode
    contract holds for both "var unset" and "var set to empty".

    **Webhook-path coupling** — the hermetic URL's path is composed from
    the runtime ``TELEGRAM_WEBHOOK_PATH`` env-var (default
    ``/v1/telegram/webhook``) so an operator who overrides the path AND
    enables skip-mode without supplying ``TELEGRAM_WEBHOOK_URL`` still
    passes :class:`TelegramSettings`'s ``_validate_url_path_matches_route``
    invariant (which compares ``webhook_url.path`` ≡ ``webhook_path``).

    No-op when ``TELEGRAM_SKIP_WEBHOOK_SET`` is unset / falsy — preserves
    the fail-closed default for production deploys.
    """
    raw_skip = os.environ.get(_HERMETIC_SKIP_ENV_VAR, "").strip().lower()
    if raw_skip not in _HERMETIC_SKIP_TRUTHY:
        return
    # Mirror the field-level default so the operator override picks through.
    webhook_path = (
        os.environ.get("TELEGRAM_WEBHOOK_PATH", _DEFAULT_WEBHOOK_PATH).strip()
        or _DEFAULT_WEBHOOK_PATH
    )
    hermetic_url = f"{_HERMETIC_WEBHOOK_URL_HOST}{webhook_path}"
    # Use direct assignment when current value is unset OR empty/whitespace
    # (setdefault alone would no-op on explicitly-empty env-vars).
    if not os.environ.get("TELEGRAM_WEBHOOK_URL", "").strip():
        os.environ["TELEGRAM_WEBHOOK_URL"] = hermetic_url
    if not os.environ.get("TELEGRAM_WEBHOOK_SECRET_TOKEN", "").strip():
        os.environ["TELEGRAM_WEBHOOK_SECRET_TOKEN"] = HERMETIC_WEBHOOK_SECRET_TOKEN


# Back-compat exports: callers that imported HERMETIC_WEBHOOK_URL directly
# now get a path-composed value via a small accessor. Most call-sites just
# observed it as documentation; the test fixture didn't depend on a
# specific path. Kept as a constant for the default-path case (which is
# what tests actually want) so the test_lifespan asserts stay readable.
HERMETIC_WEBHOOK_URL: str = f"{_HERMETIC_WEBHOOK_URL_HOST}{_DEFAULT_WEBHOOK_PATH}"


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
    # Story 11.3.7 / AC2 / D2-A — hermetic test-mode opt-in.
    #
    # When true, the lifespan SKIPS the live ``bot.set_webhook(...)`` call
    # and the operator is not required to supply real
    # ``TELEGRAM_WEBHOOK_URL`` / ``TELEGRAM_WEBHOOK_SECRET_TOKEN`` values
    # (see :func:`apply_hermetic_defaults_to_env`). Defaults to False so
    # production deploys are unaffected (set_webhook still runs, missing
    # webhook env-vars still fail-closed at boot).
    #
    # Intended only for hermetic CI / S-4 separability tests that don't
    # exercise the real Telegram webhook path. DO NOT enable in production.
    telegram_skip_webhook_set: bool = Field(
        default=False,
        validation_alias="TELEGRAM_SKIP_WEBHOOK_SET",
        description=(
            "Hermetic test-mode opt-in. When true, the lifespan skips "
            "bot.set_webhook(...) and treats TELEGRAM_WEBHOOK_URL + "
            "TELEGRAM_WEBHOOK_SECRET_TOKEN as optional. Production unset."
        ),
    )
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
    # Story 3.3 / FR1 / FR28: base URL for registry-api HTTP calls.
    # Default points at the docker-compose service name (internal network,
    # HTTP is fine for intra-compose traffic — no HTTPS requirement).
    # Override for non-compose deployments, e.g. REGISTRY_API_BASE_URL=http://localhost:8080.
    # Trailing slash is normalised away by httpx when joining paths.
    registry_api_base_url: HttpUrl = Field(
        default=HttpUrl("http://registry-api:8080"),
        validation_alias="REGISTRY_API_BASE_URL",
        description=(
            "Base URL for registry-api HTTP calls. Default points at the "
            "docker-compose service name. Override for non-compose deployments."
        ),
    )

    # Story 9.7 pass-2 TH-B1: per-chat allowlist for /trace defense-in-depth.
    # Distinct from ``tg_allowlist_user_ids`` (user-level): /trace exposes full
    # causal chains (secret.accessed, tier3.action_attempted payloads), so a
    # per-chat allowlist guards against forwarded/group-added scenarios where
    # an allowlisted USER ends up in a non-allowlisted CHAT. Empty default
    # falls back to ``tg_allowlist_user_ids`` (treated as a per-chat ids set
    # for the common 1:1 DM case where chat_id == user_id).
    #
    # Env-var: ``TRACE_ALLOWED_CHAT_IDS=123,456`` (bare CSV) or JSON list form
    # ``TRACE_ALLOWED_CHAT_IDS=[123,456]``. Empty string / unset → falls back
    # to ``tg_allowlist_user_ids`` (which itself defaults to closed/empty).
    trace_allowed_chat_ids: Any = Field(
        default_factory=frozenset,
        validation_alias="TRACE_ALLOWED_CHAT_IDS",
        description=(
            "Per-chat allowlist for /trace command. Defense-in-depth on top "
            "of tg_allowlist_user_ids. Empty default falls back to "
            "tg_allowlist_user_ids in the lifespan wiring (TH-B1)."
        ),
    )

    # Story 3.2 / FR11 / NFR-S4: allowlist of Telegram user ids.
    # Closed-by-default — empty frozenset rejects every inbound update
    # (including the operator's own id). The lifespan emits a startup
    # WARNING when this set is empty so the operator notices their
    # ``.env`` is incomplete on first boot.
    #
    # The field is declared as ``Any`` (not ``frozenset[int]``) so that
    # pydantic-settings does NOT try to JSON-parse the raw env-var string
    # before our field_validator runs. Without this, pydantic-settings'
    # ``prepare_field_value`` calls ``json.loads("")`` on an empty string
    # and raises an opaque ``SettingsError`` (H3). The field_validator
    # below handles all normalisation + type conversion itself.
    tg_allowlist_user_ids: Any = Field(
        default_factory=frozenset,
        validation_alias="TG_ALLOWLIST_USER_IDS",
        description=(
            "JSON list of allowed Telegram user ids. Empty default = "
            "closed-by-default (rejects every inbound update). FR11."
        ),
    )

    @field_validator("trace_allowed_chat_ids", mode="before")
    @classmethod
    def _validate_trace_allowed_chat_ids(cls, value: Any) -> frozenset[int]:
        """Normalise + validate the trace per-chat allowlist (TH-B1; pass-3 UH-1).

        Same parsing rules as ``tg_allowlist_user_ids``: empty string → empty
        frozenset (fallback applied in lifespan), bare CSV → list of ints,
        JSON list parsed normally. Bool values rejected; ``0`` rejected.

        Pass-3 UH-1: NEGATIVE chat_ids are valid (Telegram group chats use
        negative ids; supergroups start at ``-100...``). The earlier
        ``i <= 0`` reject made group allowlists impossible. Only ``i == 0``
        (the Telegram-undefined chat id) is rejected.
        """
        import json as _json

        if isinstance(value, str):
            value = _coerce_allowlist_raw_string(value, field_name="TRACE_ALLOWED_CHAT_IDS")
            if isinstance(value, str):
                try:
                    value = _json.loads(value)
                except _json.JSONDecodeError as exc:
                    raise ValueError(
                        f"TRACE_ALLOWED_CHAT_IDS is not valid JSON: {exc}. "
                        "Use JSON list syntax, e.g. [123,456] or [-100123456789]"
                    ) from exc

        value = _coerce_allowlist_env(value)
        try:
            result = frozenset(int(i) for i in value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"trace_allowed_chat_ids must be a list of integers; got: {exc}"
            ) from exc

        iterable_value = value if hasattr(value, "__iter__") else []
        bad_bools = [i for i in iterable_value if isinstance(i, bool)]
        if bad_bools:
            raise ValueError(
                f"trace_allowed_chat_ids must contain integers, not booleans; got {bad_bools!r}"
            )

        # Pass-3 UH-1: allow negative ids (Telegram group / supergroup chat
        # ids are negative). Reject only ``0`` (undefined chat id in
        # Telegram's API).
        bad = [i for i in result if i == 0]
        if bad:
            raise ValueError(
                f"trace_allowed_chat_ids must not contain 0 (undefined Telegram chat id); "
                f"got: {sorted(bad)!r}"
            )
        return result

    @field_validator("tg_allowlist_user_ids", mode="before")
    @classmethod
    def _validate_allowlist_positive(cls, value: Any) -> frozenset[int]:
        """Normalise + validate the allowlist field (H3 / M1 / M2 / M3 / AC-2).

        Runs BEFORE pydantic type coercion. Handles all env-var string forms:

        * ``""`` → ``frozenset()`` (closed-by-default)
        * ``"null"`` / ``"None"`` → ``frozenset()`` with INFO log (M2)
        * JSON array ``"[12345, 67890]"`` → parsed and converted (normal path)
        * bare CSV ``"12345,67890"`` → coerced permissively + INFO log (M3)
        * ``[true]`` → raises ValueError (M1)
        * ids ``<= 0`` → raises ValueError (AC-2)
        """
        import json as _json

        if isinstance(value, str):
            value = _coerce_allowlist_raw_string(value)
            # If the string was a JSON array, parse it now.
            if isinstance(value, str):
                try:
                    value = _json.loads(value)
                except _json.JSONDecodeError as exc:
                    raise ValueError(
                        f"TG_ALLOWLIST_USER_IDS is not valid JSON: {exc}. "
                        "Use JSON list syntax, e.g. [12345,67890]"
                    ) from exc

        # At this point value is a list, frozenset, set, or other iterable.
        # Apply _coerce_allowlist_env for bool rejection.
        value = _coerce_allowlist_env(value)

        # Convert to frozenset[int].
        try:
            result = frozenset(int(i) for i in value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"tg_allowlist_user_ids must be a list of integers; got: {exc}"
            ) from exc

        # Reject bool coercion: [true] → int(True) == 1 but isinstance(True, bool).
        iterable_value = value if hasattr(value, "__iter__") else []
        bad_bools = [i for i in iterable_value if isinstance(i, bool)]
        if bad_bools:
            raise ValueError(
                f"tg_allowlist_user_ids must contain integers, not booleans; got {bad_bools!r}"
            )

        # Reject non-positive ids (AC-2).
        bad = [i for i in result if i <= 0]
        if bad:
            raise ValueError(
                f"tg_allowlist_user_ids must contain positive integers; "
                f"got non-positive value(s): {sorted(bad)!r}"
            )
        return result

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
            # Story 11.3.8 / FR62a: use ``ensure_shared_dir`` so this probe
            # leaves the dir at mode 0o2775 if it's the first creator —
            # avoids the cross-uid permission lockout other ``omb``-group
            # services hit when telegram-gateway wins the boot race.
            ensure_shared_dir(target)
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
