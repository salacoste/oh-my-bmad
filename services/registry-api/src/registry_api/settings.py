"""Approval-signing settings (Story 11.1 / FR64 / NFR-S10).

This is the first ``pydantic-settings`` consumer in ``registry-api``.
Prior stories threaded all configuration through ``build_app(...)``
keyword arguments and direct ``os.environ`` reads (see ``app.py``'s
``ANTHROPIC_API_KEY`` handling).  Story 11.1 introduces a dedicated
:class:`ApprovalSigningSettings` class instead of extending a global
``RegistryApiSettings`` (none exists) — the smaller surface keeps the
HMAC-key blast radius tight.

Design notes:

* **No env_prefix** — the env-var is ``OPERATOR_HMAC_KEY`` (matches FR64
  wording — the key is operator-property, not registry-api property, so
  no ``REGISTRY_API_`` prefix is applied).
* **``SecretStr`` wrapping** — Pydantic's :class:`SecretStr` masks the
  value in ``repr()`` / ``model_dump()`` / log records by default
  (NFR-S10 isolation). Only :func:`compute_approval_hmac` is allowed to
  call ``.get_secret_value()``.
* **``min_length=32`` on the inner string** — enforced via Pydantic
  ``Field`` when the env-var IS set. 32 bytes / 256 bits is the
  HMAC-SHA256 minimum for a non-trivial keyspace.  Recommendation
  documented in ``.env.example``: ``openssl rand -hex 32`` (64 hex
  characters).
* **``default=None``** — a missing key is NOT a startup error.  The
  ``/decisions`` handler logs ``approval_signing_disabled_missing_hmac_key``
  and emits ``approval.granted`` WITHOUT a paired
  ``task.approval_signed`` sibling.  This is a deliberate safety
  trade-off (D2 in Story 11.1 spec): approvals are operational
  primitives and must not be blocked by missing audit-signing config.

Hot-reload is NOT supported.  The key is read once at service startup
(via :py:meth:`ApprovalSigningSettings.from_env`).  Operators must
restart the service to pick up a rotated ``.env`` value.  Story 11.5
will formalize the rotation flow with a ``key.rotated`` audit event.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApprovalSigningSettings(BaseSettings):
    """Settings for HMAC signing of approval events (FR64 / NFR-S10).

    Construct via :py:meth:`ApprovalSigningSettings.from_env` so the
    env-var resolution happens in one well-known place.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        # Inherit ``extra="ignore"`` so unrelated env-vars in the shared
        # process / .env file do not break instantiation.
        extra="ignore",
        # No env_prefix — the canonical env-var is OPERATOR_HMAC_KEY.
        # ``populate_by_name=True`` lets construction by Python field name
        # (e.g. ``ApprovalSigningSettings(operator_hmac_key=...)``) work in
        # addition to the env-var alias.  Without this, only the alias name
        # is accepted at instance-construction time which is brittle for
        # tests that inject explicit settings.
        populate_by_name=True,
    )

    operator_hmac_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPERATOR_HMAC_KEY",
        description=(
            "Operator-local HMAC-SHA256 signing key for approval events "
            "(FR64). When set, every approval.granted event is paired with "
            "a task.approval_signed sibling carrying the HMAC digest. "
            "When unset, signing is skipped and a structured warning is "
            "logged. Recommend: 64-char hex from `openssl rand -hex 32` "
            "(NFR-S10 isolation — value MUST NOT leak to logs/events/"
            "snapshots; pydantic.SecretStr default masking enforces this)."
        ),
    )

    @field_validator("operator_hmac_key", mode="after")
    @classmethod
    def _enforce_min_length(cls, value: SecretStr | None) -> SecretStr | None:
        """Reject too-short keys (≥ 32 chars) when the env-var IS set.

        Pydantic ``Field(min_length=...)`` on ``SecretStr | None`` is
        permissive on ``None`` but applies to the inner string.  We
        re-implement it as a ``field_validator`` so the error message
        names the env-var (operator-facing clarity).  ``None`` (unset)
        is explicitly permitted — the handler emits a warning and skips
        signing in that case.
        """
        if value is None:
            return None
        # SecretStr stores the raw string; access via get_secret_value()
        # ONLY for the length check.  The value is not logged.
        raw = value.get_secret_value()
        if len(raw) < 32:
            raise ValueError(
                "OPERATOR_HMAC_KEY must be at least 32 characters when set "
                "(recommend 64-char hex from `openssl rand -hex 32`); "
                f"got length={len(raw)}"
            )
        return value

    @classmethod
    def from_env(cls) -> ApprovalSigningSettings:
        """Construct from the process environment.

        Single entry point so the env-var resolution site is greppable
        and the BaseSettings instantiation pattern matches the
        ``telegram-gateway`` convention.
        """
        return cls()


__all__ = ["ApprovalSigningSettings"]
