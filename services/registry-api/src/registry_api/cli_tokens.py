"""Operator JWT token generation utility (Story 6.1+).

Usage::

    # Generate a token for operator "alice" (24h default expiry)
    python -m registry_api.cli_tokens generate --actor-id alice

    # Custom expiry
    python -m registry_api.cli_tokens generate --actor-id alice --expire-minutes 60

    # Verify a token (prints decoded claims)
    python -m registry_api.cli_tokens verify --token <jwt-string>

Requires ``JWT_SECRET_KEY`` env var to be set.  Exits with code 1 and an
error message if the env var is missing or too short.

Design notes:

* **HS256 only** — Phase 1 symmetric signing.  The generated token includes
  ``iss`` (issuer), ``sub`` (actor_id), ``iat`` (issued-at), and ``exp``
  (expiry) claims.
* **No file output** — tokens are printed to stdout so operators can pipe
  them into configs or clipboards.  stderr is used for diagnostics.
* **Idempotent** — running generate multiple times produces different tokens
  (different ``iat`` / ``jti`` values), all of which validate correctly.
"""

from __future__ import annotations

import argparse
import datetime
import sys
import uuid

import jwt as pyjwt

from registry_api.settings import JwtAuthSettings


def generate_token(
    *,
    actor_id: str,
    settings: JwtAuthSettings,
    expire_minutes: int | None = None,
) -> str:
    """Generate a signed JWT for the given actor_id.

    Args:
        actor_id: Operator identifier (becomes the ``sub`` claim).
        settings: JWT auth settings (provides secret, issuer, algorithm).
        expire_minutes: Override token lifetime. Uses settings default when None.

    Returns:
        Encoded JWT string.

    Raises:
        SystemExit: If JWT_SECRET_KEY is not configured.
    """
    if not settings.enabled:
        print(
            "ERROR: JWT_SECRET_KEY is not set. Set it via env var or .env file.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    assert settings.jwt_secret_key is not None  # guaranteed by .enabled
    secret = settings.jwt_secret_key.get_secret_value()

    ttl = expire_minutes if expire_minutes is not None else settings.access_token_expire_minutes
    now = datetime.datetime.now(datetime.UTC)
    payload = {
        "iss": settings.issuer,
        "sub": actor_id,
        "iat": now,
        "exp": now + datetime.timedelta(minutes=ttl),
        "jti": str(uuid.uuid4()),
    }

    token = pyjwt.encode(payload, secret, algorithm=settings.algorithm)
    return token


def verify_token(
    *,
    token: str,
    settings: JwtAuthSettings,
) -> dict[str, object]:
    """Decode and verify a JWT token, returning its claims.

    Args:
        token: Encoded JWT string.
        settings: JWT auth settings.

    Returns:
        Decoded claims dictionary.

    Raises:
        SystemExit: If JWT_SECRET_KEY is not configured or token is invalid.
    """
    if not settings.enabled:
        print(
            "ERROR: JWT_SECRET_KEY is not set.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    assert settings.jwt_secret_key is not None
    secret = settings.jwt_secret_key.get_secret_value()

    try:
        payload = pyjwt.decode(
            token,
            secret,
            algorithms=[settings.algorithm],
            options={"require": ["exp", "sub", "iss"]},
            issuer=settings.issuer,
            leeway=settings.leeway_seconds,
        )
    except pyjwt.ExpiredSignatureError:
        print("ERROR: Token has expired.", file=sys.stderr)
        raise SystemExit(1) from None
    except pyjwt.InvalidTokenError as exc:
        print(f"ERROR: Invalid token: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    return payload


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="registry_api.cli_tokens",
        description="JWT token generation and verification for oh-my-bmad registry API.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # generate
    gen = sub.add_parser("generate", help="Generate a new JWT token")
    gen.add_argument(
        "--actor-id",
        required=True,
        help="Operator identifier (becomes the 'sub' claim in the token).",
    )
    gen.add_argument(
        "--expire-minutes",
        type=int,
        default=None,
        help="Token lifetime in minutes (default: 1440 = 24h).",
    )

    # verify
    ver = sub.add_parser("verify", help="Verify and decode a JWT token")
    ver.add_argument(
        "--token",
        required=True,
        help="JWT token string to verify.",
    )

    args = parser.parse_args(argv)
    settings = JwtAuthSettings.from_env()

    if args.command == "generate":
        token = generate_token(
            actor_id=args.actor_id,
            settings=settings,
            expire_minutes=args.expire_minutes,
        )
        # Print token to stdout; metadata to stderr.
        print(
            f"Token generated for actor '{args.actor_id}' "
            f"(expires in {args.expire_minutes or settings.access_token_expire_minutes}m, "
            f"issuer={settings.issuer}, alg={settings.algorithm})",
            file=sys.stderr,
        )
        print(token)
    elif args.command == "verify":
        payload = verify_token(token=args.token, settings=settings)
        for key, value in sorted(payload.items()):
            print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
