"""GET /.well-known/jwks.json and /.well-known/openid-configuration (Story 6.1+).

JWKS endpoint exposes the public key information needed by callers to verify
JWT tokens issued by this registry API.  For Phase 1 (HS256 symmetric),
the JWKS entry contains the key ID and algorithm — the shared secret is NOT
exposed (callers that need to verify tokens locally must be configured with
the same ``JWT_SECRET_KEY``).

The discovery endpoint (``openid-configuration``) provides the standard
``issuer``, ``jwks_uri``, and supported ``id_token_signing_alg_values``
so callers can auto-configure.

Both endpoints are mounted at ``/.well-known/`` (no ``/v1`` prefix) per
the RFC 8414 / OpenID Connect Discovery convention.

Design notes:

* **HS256 JWKS** — symmetric keys do not have a true "public key" to expose.
  The JWKS entry contains the key ID (derived from a truncated SHA-256
  fingerprint of the secret) and the algorithm.  This allows callers to:
  (a) confirm the expected algorithm, and (b) match ``kid`` in tokens
  against the published set for rotation awareness.
* **Unauthenticated** — both endpoints are read-only and contain no secrets.
  They sit outside the ``/v1`` prefix so the ``JwtAuthMiddleware`` (which
  applies to ``/v1/*`` routes) does not gate them.
* **Disabled when JWT is off** — when ``JWT_SECRET_KEY`` is not configured,
  ``jwks_json`` returns an empty ``keys`` array and ``openid_configuration``
  omits the ``jwks_uri`` field.  This prevents callers from erroneously
  believing JWT auth is active.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Request
from pydantic import BaseModel

from registry_api.settings import JwtAuthSettings

router = APIRouter()


class JWK(BaseModel):
    """A single JSON Web Key entry."""

    kty: str
    kid: str
    alg: str
    use: str


class JWKSResponse(BaseModel):
    """RFC 7517 JSON Web Key Set response."""

    keys: list[JWK]


class OpenIDConfiguration(BaseModel):
    """OpenID Connect Discovery 1.0 response (partial)."""

    issuer: str
    jwks_uri: str | None = None
    id_token_signing_alg_values_supported: list[str] | None = None
    subject_types_supported: list[str] = ["public"]
    response_types_supported: list[str] = ["id_token"]
    claims_supported: list[str] = ["sub", "iss", "exp", "iat"]


def _derive_kid(secret: str) -> str:
    """Derive a key ID from the secret via truncated SHA-256 fingerprint.

    Uses the first 16 hex chars (64 bits) — short enough for ``kid`` fields
    but collision-resistant in practice.  The fingerprint is one-way: the
    secret cannot be recovered from it.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]


@router.get("/.well-known/jwks.json", response_model=JWKSResponse)
async def jwks_json(request: Request) -> JWKSResponse:
    """Serve the JSON Web Key Set for token verification.

    Returns an empty ``keys`` array when JWT auth is not configured.
    """
    settings = _resolve_settings(request)
    if not settings.enabled:
        return JWKSResponse(keys=[])

    assert settings.jwt_secret_key is not None  # guaranteed by .enabled
    kid = _derive_kid(settings.jwt_secret_key.get_secret_value())
    key = JWK(
        kty="oct",  # symmetric key type (HS256)
        kid=kid,
        alg=settings.algorithm,
        use="sig",
    )
    return JWKSResponse(keys=[key])


@router.get("/.well-known/openid-configuration", response_model=OpenIDConfiguration)
async def openid_configuration(request: Request) -> OpenIDConfiguration:
    """Serve OpenID Connect Discovery configuration.

    Returns minimal fields; ``jwks_uri`` is omitted when JWT is not configured.
    """
    settings = _resolve_settings(request)
    if not settings.enabled:
        return OpenIDConfiguration(
            issuer=settings.issuer,
        )

    # Derive jwks_uri from the incoming request's base URL.
    # This avoids hardcoding host/port and works behind proxies.
    base_url = str(request.base_url).rstrip("/")
    jwks_uri = f"{base_url}/.well-known/jwks.json"

    return OpenIDConfiguration(
        issuer=settings.issuer,
        jwks_uri=jwks_uri,
        id_token_signing_alg_values_supported=[settings.algorithm],
    )


def _resolve_settings(request: Request) -> JwtAuthSettings:
    """Resolve JwtAuthSettings from app.state (set by lifespan) or env.

    The middleware stores resolved settings at construction time, but the
    routes don't have direct access to the middleware instance.  We store
    the settings on ``app.state.jwt_settings`` during lifespan startup
    (if available) or fall back to ``JwtAuthSettings.from_env()``.
    """
    stored: JwtAuthSettings | None = getattr(request.app.state, "jwt_settings", None)
    if stored is not None:
        return stored
    return JwtAuthSettings.from_env()
