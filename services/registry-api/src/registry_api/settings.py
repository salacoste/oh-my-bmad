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
  (NFR-S10 isolation). ``.get_secret_value()`` is called in EXACTLY TWO
  places: the ``_enforce_min_length`` validator (transient frame-local,
  not logged — safe per NFR-S10) AND :func:`compute_approval_hmac` (pure
  function, value stays in-frame — safe per NFR-S10).  Both are safe;
  future reviewers should not flag the validator as a NFR-S10 violation
  (P1-L3 clarification).
* **32 bytes (UTF-8 encoded) / 256 bits minimum** — enforced via
  ``_enforce_min_length`` byte-count check (P1-M4).  For the canonical
  recipe (``openssl rand -hex 32`` → 64 ASCII hex chars = 64 bytes) the
  byte count equals the character count.  The byte-count validation is
  the correct cryptographic invariant.  Empty/whitespace env-var values
  are normalised to ``None`` (P1-M2 — "unset" semantics) before the
  length check fires.
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
        """Normalise and validate OPERATOR_HMAC_KEY when set.

        Pydantic ``Field(min_length=...)`` on ``SecretStr | None`` is
        permissive on ``None`` but applies to the inner string.  We
        re-implement as a ``field_validator`` for three reasons:

        1. **Operator-facing error message** — names the env-var explicitly.
        2. **Empty/whitespace normalisation (P1-M2)** — ``OPERATOR_HMAC_KEY=""``
           is parsed by Pydantic as ``SecretStr("")`` (not ``None``).  We
           normalise empty/whitespace values to ``None`` so operators who
           clear the env-var get the expected "signing disabled" path
           rather than a confusing "too short" validation error.
        3. **Byte-count validation (P1-M4)** — enforces 32 bytes (UTF-8
           encoded) / 256 bits minimum rather than 32 characters.  For the
           canonical recipe (``openssl rand -hex 32`` → 64 ASCII hex chars)
           character count equals byte count.  Byte-count is the correct
           cryptographic invariant.

        ``None`` (unset) is explicitly permitted — the handler emits a
        warning and skips signing in that case.

        Security note: ``.get_secret_value()`` is called here (transient
        frame-local — value is NOT logged) and in :func:`compute_approval_hmac`
        (pure function — value stays in-frame).  Both call sites are safe
        per NFR-S10.  See module docstring for full accounting (P1-L3).
        """
        if value is None:
            return None
        # SecretStr stores the raw string; access via get_secret_value()
        # ONLY for normalisation + length check.  The value is not logged.
        raw = value.get_secret_value()
        # P1-M2: empty/whitespace → treat as unset.
        if not raw.strip():
            return None
        # P1-M4: enforce byte count (cryptographic invariant), not char count.
        raw_bytes = raw.encode("utf-8")
        if len(raw_bytes) < 32:
            raise ValueError(
                "OPERATOR_HMAC_KEY must be at least 32 BYTES (UTF-8 encoded) "
                "/ 256 bits minimum when set "
                "(recommend 64-char hex from `openssl rand -hex 32` — 64 bytes); "
                f"got {len(raw_bytes)} bytes"
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


class HealthProbeSettings(BaseSettings):
    """Settings for the GET /v1/health probes (Story 11.3.9 / FR17 / NFR-R8).

    Story 11.3.9 committed (AC2/AC3 + Dev Notes) to making the worker
    look-back window and the queue-depth look-back operator-tunable via
    env vars, wired through the same pydantic-settings pattern as
    :class:`ApprovalSigningSettings` rather than direct ``os.environ``
    reads (the codebase's NO-`os.environ.copy()` discipline).

    Both windows are bounded:

    * ``worker_window_s`` ∈ [5, 3600] — narrower than 5s flips between
      "ok"/"idle" on heartbeat jitter; wider than 1h makes "ok"
      meaningless. Default 60 (matches
      ``health_probes.WORKER_WINDOW_S_DEFAULT``).
    * ``queue_lookback_s`` ∈ [5, 86400] — covers worker-pickup latency;
      pending tasks older than this are "stuck" (a different alert).
      Default 300 (matches ``health_probes.QUEUE_LOOKBACK_S_DEFAULT``).

    Out-of-range env values raise at construction time (fail-fast at
    startup, not silently clamped — an operator who typo'd 36000 for
    3600 should hear about it, not get a silently-different window).
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        extra="ignore",
        populate_by_name=True,
    )

    worker_window_s: int = Field(
        default=60,
        ge=5,
        le=3600,
        validation_alias="OMB_HEALTH_WORKER_WINDOW_S",
        description=(
            "Look-back window (seconds) for the worker-activity health probe "
            "(AC2). A worker event within this window → worker_status='ok'; "
            "none → 'idle'. Default 60."
        ),
    )
    queue_lookback_s: int = Field(
        default=300,
        ge=5,
        le=86_400,
        validation_alias="OMB_HEALTH_QUEUE_LOOKBACK_S",
        description=(
            "Look-back window (seconds) for the queue-depth health probe "
            "(AC3). Pending tasks created within this window are counted; "
            "older ones are treated as stuck (out of scope). Default 300."
        ),
    )

    @classmethod
    def from_env(cls) -> HealthProbeSettings:
        """Construct from the process environment.

        Single greppable entry point — matches :meth:`ApprovalSigningSettings.from_env`.
        """
        return cls()


class JwtAuthSettings(BaseSettings):
    """Settings for JWT authentication on the registry HTTP API (Story 6.1+).

    Replaces the Phase 1 ``X-Actor-Id`` header-trust model with cryptographic
    token validation.  When ``JWT_SECRET_KEY`` is configured, the
    ``JwtAuthMiddleware`` validates ``Authorization: Bearer <token>`` headers
    and extracts ``actor_id`` from the ``sub`` claim.  When unset, the
    middleware falls back to the Phase 1 ``X-Actor-Id`` header-trust behaviour
    (backward compatible — no operator disruption during rollout).

    Design notes:

    * **HS256 symmetric** — Phase 1 uses a shared secret (HMAC-SHA256) for
      simplicity.  RS256 (asymmetric) can be added in a follow-up by extending
      this settings class with ``JWT_PUBLIC_KEY`` / ``JWT_ALGORITHM`` fields.
    * **``SecretStr`` wrapping** — mirrors :class:`ApprovalSigningSettings`;
      the secret key is masked in ``repr()`` / ``model_dump()`` / logs.
    * **``default=None``** — a missing key is NOT a startup error.  When
      ``None``, auth is disabled and the ``X-Actor-Id`` header-trust path
      runs instead (backward compatible).
    * **Token defaults** — ``access_token_expire_minutes=1440`` (24 hours);
      ``algorithm="HS256"``; ``issuer="oh-my-bmad/registry-api"``.
    * **Clock skew** — ``leeway_seconds=30`` for token expiry validation to
      tolerate minor clock drift between services.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        extra="ignore",
        populate_by_name=True,
    )

    jwt_secret_key: SecretStr | None = Field(
        default=None,
        validation_alias="JWT_SECRET_KEY",
        description=(
            "Shared HMAC-SHA256 secret for JWT token validation (Story 6.1+). "
            "When set, the registry API requires ``Authorization: Bearer`` "
            "tokens; when unset, falls back to X-Actor-Id header trust "
            "(Phase 1 backward compat). "
            "Recommend: 64-char hex from `openssl rand -hex 32`."
        ),
    )

    algorithm: str = Field(
        default="HS256",
        validation_alias="JWT_ALGORITHM",
        description="JWT signing algorithm. HS256 (symmetric) for Phase 1.",
    )

    issuer: str = Field(
        default="oh-my-bmad/registry-api",
        validation_alias="JWT_ISSUER",
        description="Expected ``iss`` claim in validated tokens.",
    )

    access_token_expire_minutes: int = Field(
        default=1440,
        ge=1,
        le=525600,  # max 1 year
        validation_alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
        description="Default token lifetime in minutes. Used by token generation.",
    )

    leeway_seconds: int = Field(
        default=30,
        ge=0,
        le=300,
        validation_alias="JWT_LEEWAY_SECONDS",
        description=(
            "Clock-skew tolerance in seconds for token expiry validation. "
            "Accounts for minor clock drift between services."
        ),
    )

    @field_validator("jwt_secret_key", mode="after")
    @classmethod
    def _enforce_min_length(cls, value: SecretStr | None) -> SecretStr | None:
        """Normalise and validate JWT_SECRET_KEY when set.

        Mirrors :meth:`ApprovalSigningSettings._enforce_min_length`:
        empty/whitespace normalised to ``None``; byte-count enforced at
        32 bytes (256 bits) minimum for HMAC-SHA256 security.
        """
        if value is None:
            return None
        raw = value.get_secret_value()
        if not raw.strip():
            return None
        raw_bytes = raw.encode("utf-8")
        if len(raw_bytes) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 BYTES (UTF-8 encoded) "
                "/ 256 bits minimum when set "
                f"(got {len(raw_bytes)} bytes); "
                "recommend 64-char hex from `openssl rand -hex 32`"
            )
        return value

    @property
    def enabled(self) -> bool:
        """True when JWT auth is configured (secret key is set)."""
        return self.jwt_secret_key is not None

    @classmethod
    def from_env(cls) -> JwtAuthSettings:
        """Construct from the process environment."""
        return cls()


__all__ = ["ApprovalSigningSettings", "HealthProbeSettings", "JwtAuthSettings"]
