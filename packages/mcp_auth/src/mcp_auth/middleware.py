"""Raw ASGI bearer token middleware for MCP Streamable HTTP transport (Phase 10).

Validates ``Authorization: Bearer <token>`` on every incoming ASGI request
using PyJWT.  Uses raw ASGI (NOT Starlette ``BaseHTTPMiddleware``) for
performance — the MCP transport path should not pay the overhead of the
Starlette request/response wrapper stack.

On successful validation:
  * Injects ``X-Actor-Id: {sub}`` into the ASGI scope headers so downstream
    MCP handlers can read it without touching the JWT library.
  * Sets ``scope["state"]["actor_id"]`` and
    ``scope["state"]["authenticated"] = True`` for any Starlette-layer
    consumers that inspect request.state.

On validation failure:
  * Returns HTTP 401 with JSON body ``{"error": "unauthorized", "detail": "..."}``.

When ``McpAuthSettings.enabled`` is False (no ``JWT_SECRET_KEY`` configured):
  * Passes every request through without auth.  The stdio transport never
    mounts this middleware, so this path is a safety net for misconfigured
    HTTP deployments.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import jwt as pyjwt
import structlog

from mcp_auth.settings import McpAuthSettings

_log = logging.getLogger("mcp_auth.middleware")

# ASGI scope type for HTTP requests.
_HTTP = "http"

# Header name constants (lowercased for ASGI header matching).
_AUTHORIZATION = b"authorization"
_X_ACTOR_ID = b"x-actor-id"

# Health probe path — skip auth to allow load-balancer checks.
_HEALTHZ = "/healthz"


class BearerTokenMiddleware:
    """Validate ``Authorization: Bearer <token>`` on every HTTP request.

    - Extracts ``actor_id`` from the ``sub`` claim.
    - Returns 401 JSON on missing/invalid/expired tokens.
    - Injects ``X-Actor-Id: {sub}`` header into the ASGI scope on success.
    - Skips auth for ``GET /healthz`` (health probe endpoint).
    - When ``settings.enabled`` is False, passes through without auth.
    """

    def __init__(self, app: Any, settings: McpAuthSettings) -> None:
        self._app = app
        self._settings = settings

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        if scope["type"] != _HTTP:
            await self._app(scope, receive, send)
            return

        # Health probe — skip auth.
        path: str = scope.get("path", "")
        method: str = scope.get("method", "")
        if method == "GET" and path == _HEALTHZ:
            await self._app(scope, receive, send)
            return

        # Auth disabled (no JWT_SECRET_KEY) — pass through.
        if not self._settings.enabled:
            await self._app(scope, receive, send)
            return

        # Extract Authorization header from ASGI scope.
        headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        auth_value: bytes | None = None
        for name, value in headers:
            if name.lower() == _AUTHORIZATION:
                auth_value = value
                break

        if auth_value is None:
            await self._send_unauthorized(send, "Missing Authorization header")
            return

        # Validate "Bearer <token>" prefix.
        if not auth_value.startswith(b"Bearer "):
            await self._send_unauthorized(send, "Malformed Authorization header")
            return

        token = auth_value[len(b"Bearer "):].strip()
        if not token:
            await self._send_unauthorized(send, "Empty bearer token")
            return

        # Decode and validate JWT.
        assert self._settings.jwt_secret_key is not None  # guaranteed by .enabled
        try:
            payload = pyjwt.decode(
                token,
                self._settings.jwt_secret_key.get_secret_value(),
                algorithms=[self._settings.algorithm],
                options={
                    "require": ["exp", "sub", "iss"],
                    "verify_exp": True,
                    "verify_iss": True,
                },
                issuer=self._settings.issuer,
                leeway=self._settings.leeway_seconds,
            )
        except pyjwt.ExpiredSignatureError:
            structlog.get_logger().warning(
                "jwt_token_expired", path=path, method=method,
            )
            await self._send_unauthorized(send, "Token has expired")
            return
        except pyjwt.InvalidIssuerError:
            structlog.get_logger().warning(
                "jwt_token_invalid_issuer", path=path, method=method,
            )
            await self._send_unauthorized(send, "Invalid token issuer")
            return
        except pyjwt.MissingRequiredClaimError as exc:
            structlog.get_logger().warning(
                "jwt_token_missing_claim",
                path=path, method=method, missing_claim=str(exc),
            )
            await self._send_unauthorized(send, f"Missing required claim: {exc}")
            return
        except pyjwt.InvalidSignatureError:
            structlog.get_logger().warning(
                "jwt_token_invalid_signature", path=path, method=method,
            )
            await self._send_unauthorized(send, "Invalid token signature")
            return
        except pyjwt.InvalidTokenError as exc:
            structlog.get_logger().warning(
                "jwt_token_invalid", path=path, method=method, error=str(exc),
            )
            await self._send_unauthorized(send, f"Invalid token: {exc}")
            return

        # Extract actor_id from sub claim.
        actor_id = payload.get("sub")
        if not actor_id or not isinstance(actor_id, str):
            structlog.get_logger().warning(
                "jwt_token_invalid_sub", path=path, method=method,
            )
            await self._send_unauthorized(send, "Invalid or missing 'sub' claim")
            return

        # Inject X-Actor-Id header into ASGI scope.
        actor_bytes = actor_id.encode("utf-8")
        headers = list(headers)  # copy to mutate
        headers.append((_X_ACTOR_ID, actor_bytes))
        scope["headers"] = headers

        # Set scope state for Starlette-layer consumers.
        state = scope.setdefault("state", {})
        state["actor_id"] = actor_id
        state["authenticated"] = True

        await self._app(scope, receive, send)

    @staticmethod
    async def _send_unauthorized(send: Any, detail: str) -> None:
        """Send HTTP 401 JSON response."""
        body = json.dumps({"error": "unauthorized", "detail": detail}).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode("utf-8")],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })
