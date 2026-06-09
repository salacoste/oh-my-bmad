"""Unit tests for BearerTokenMiddleware (raw ASGI)."""

from __future__ import annotations

import json
import time
from typing import Any

import jwt as pyjwt
import pytest
from mcp_auth.middleware import BearerTokenMiddleware
from mcp_auth.settings import McpAuthSettings
from pydantic import SecretStr

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SECRET = "a" * 64  # 64-char key (>=32 bytes)
_ALGORITHM = "HS256"
_ISSUER = "oh-my-bmad/registry-api"


def _settings(enabled: bool = True) -> McpAuthSettings:
    if enabled:
        return McpAuthSettings(jwt_secret_key=SecretStr(_SECRET))
    return McpAuthSettings()  # jwt_secret_key=None → disabled


def _make_token(
    sub: str = "test-actor",
    exp: float | None = None,
    iss: str = _ISSUER,
    extra: dict[str, Any] | None = None,
) -> bytes:
    """Build a signed JWT.  Returns *bytes* so callers can pass it directly as
    an Authorization header value after prepending ``b"Bearer "``."""
    now = time.time()
    payload: dict[str, Any] = {
        "sub": sub,
        "iss": iss,
        "exp": exp if exp is not None else now + 3600,
        "iat": now,
    }
    if extra:
        payload.update(extra)
    return pyjwt.encode(payload, _SECRET, algorithm=_ALGORITHM).encode("utf-8")


# ---------------------------------------------------------------------------
# ASGI test harness
# ---------------------------------------------------------------------------


class _Response:
    """Captures what the ASGI app sent back."""

    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: list[tuple[bytes, bytes]] = []
        self.body: bytes = b""

    @property
    def json(self) -> Any:
        return json.loads(self.body)


async def _invoke(
    settings: McpAuthSettings,
    *,
    scope_overrides: dict[str, Any] | None = None,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> _Response:
    """Run the middleware against a dummy inner ASGI app and capture output."""

    async def inner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        body = b"ok"
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [[b"content-type", b"text/plain"], [b"content-length", b"2"]],
        })
        await send({"type": "http.response.body", "body": body})

    resp = _Response()
    mw = BearerTokenMiddleware(app=inner_app, settings=settings)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b""}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            resp.status = message["status"]
            resp.headers = message.get("headers", [])
        elif message["type"] == "http.response.body":
            resp.body = message.get("body", b"")

    base_scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/rpc",
        "query_string": b"",
        "headers": headers or [],
    }
    if scope_overrides:
        base_scope.update(scope_overrides)

    await mw(base_scope, receive, send)
    return resp


async def _invoke_with_capture(
    settings: McpAuthSettings,
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    scope_overrides: dict[str, Any] | None = None,
) -> tuple[_Response, dict[str, Any]]:
    """Run middleware and capture both the response and the inner scope."""

    captured_scope: dict[str, Any] = {}

    async def capture_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        captured_scope.update(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = BearerTokenMiddleware(app=capture_app, settings=settings)

    resp = _Response()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b""}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            resp.status = message["status"]
            resp.headers = message.get("headers", [])
        elif message["type"] == "http.response.body":
            resp.body = message.get("body", b"")

    base_scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/rpc",
        "query_string": b"",
        "headers": headers or [],
    }
    if scope_overrides:
        base_scope.update(scope_overrides)

    await mw(base_scope, receive, send)
    return resp, captured_scope


# ===================================================================
# Tests
# ===================================================================


class TestValidToken:
    """Happy-path: a properly signed JWT passes through."""

    @pytest.mark.asyncio
    async def test_passes_through_and_injects_actor_id(self) -> None:
        token = _make_token(sub="user-42")
        resp = await _invoke(_settings(), headers=[(b"authorization", b"Bearer " + token)])

        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_x_actor_id_header_injected(self) -> None:
        token = _make_token(sub="user-42")
        resp, scope = await _invoke_with_capture(
            _settings(), headers=[(b"authorization", b"Bearer " + token)],
        )
        assert resp.status == 200
        header_dict = dict(scope["headers"])
        assert header_dict[b"x-actor-id"] == b"user-42"

    @pytest.mark.asyncio
    async def test_scope_state_set(self) -> None:
        token = _make_token(sub="test-actor")
        resp, scope = await _invoke_with_capture(
            _settings(), headers=[(b"authorization", b"Bearer " + token)],
        )

        assert resp.status == 200
        state = scope.get("state", {})
        assert state.get("actor_id") == "test-actor"
        assert state.get("authenticated") is True

    @pytest.mark.asyncio
    async def test_actor_id_extracted_from_sub(self) -> None:
        token = _make_token(sub="custom-actor-99")
        resp, scope = await _invoke_with_capture(
            _settings(), headers=[(b"authorization", b"Bearer " + token)],
        )

        header_dict = dict(scope["headers"])
        assert header_dict[b"x-actor-id"] == b"custom-actor-99"
        assert scope["state"]["actor_id"] == "custom-actor-99"


class TestExpiredToken:
    @pytest.mark.asyncio
    async def test_returns_401_token_expired(self) -> None:
        # exp far in the past, beyond leeway
        token = _make_token(exp=time.time() - 600)
        resp = await _invoke(_settings(), headers=[(b"authorization", b"Bearer " + token)])

        assert resp.status == 401
        assert "expired" in resp.json["detail"].lower()


class TestInvalidSignature:
    @pytest.mark.asyncio
    async def test_returns_401_invalid_signature(self) -> None:
        # Sign with a different secret
        bad_token = pyjwt.encode(
            {"sub": "x", "exp": time.time() + 3600, "iss": _ISSUER},
            "wrong-secret-that-is-at-least-32-bytes-long!!",
            algorithm=_ALGORITHM,
        ).encode("utf-8")
        resp = await _invoke(_settings(), headers=[(b"authorization", b"Bearer " + bad_token)])

        assert resp.status == 401
        assert "Invalid token signature" in resp.json["detail"]


class TestMissingAuthHeader:
    @pytest.mark.asyncio
    async def test_returns_401_missing_header(self) -> None:
        resp = await _invoke(_settings(), headers=[])

        assert resp.status == 401
        assert "Missing Authorization header" in resp.json["detail"]


class TestMalformedHeader:
    @pytest.mark.asyncio
    async def test_returns_401_no_bearer_prefix(self) -> None:
        resp = await _invoke(_settings(), headers=[(b"authorization", b"Basic abc123")])

        assert resp.status == 401
        assert "Malformed Authorization header" in resp.json["detail"]


class TestEmptyBearerToken:
    @pytest.mark.asyncio
    async def test_returns_401_empty_token(self) -> None:
        resp = await _invoke(_settings(), headers=[(b"authorization", b"Bearer ")])

        assert resp.status == 401
        assert "Empty bearer token" in resp.json["detail"]

    @pytest.mark.asyncio
    async def test_returns_401_whitespace_only_token(self) -> None:
        resp = await _invoke(_settings(), headers=[(b"authorization", b"Bearer   ")])

        assert resp.status == 401
        assert "Empty bearer token" in resp.json["detail"]


class TestMissingSubClaim:
    @pytest.mark.asyncio
    async def test_returns_401_no_sub(self) -> None:
        # Build a token with an empty sub — the middleware checks for falsy sub
        # after PyJWT decode succeeds (all required claims present).
        now = time.time()
        payload = {"sub": "", "iss": _ISSUER, "exp": now + 3600, "iat": now}
        token = pyjwt.encode(payload, _SECRET, algorithm=_ALGORITHM).encode("utf-8")
        resp = await _invoke(_settings(), headers=[(b"authorization", b"Bearer " + token)])

        assert resp.status == 401
        assert "Invalid or missing 'sub' claim" in resp.json["detail"]


class TestMissingExpClaim:
    @pytest.mark.asyncio
    async def test_returns_401_no_exp(self) -> None:
        now = time.time()
        payload = {"sub": "actor", "iss": _ISSUER, "iat": now}
        token = pyjwt.encode(payload, _SECRET, algorithm=_ALGORITHM).encode("utf-8")
        resp = await _invoke(_settings(), headers=[(b"authorization", b"Bearer " + token)])

        assert resp.status == 401
        assert "Missing required claim" in resp.json["detail"]


class TestMissingIssClaim:
    @pytest.mark.asyncio
    async def test_returns_401_no_iss(self) -> None:
        now = time.time()
        payload = {"sub": "actor", "exp": now + 3600, "iat": now}
        token = pyjwt.encode(payload, _SECRET, algorithm=_ALGORITHM).encode("utf-8")
        resp = await _invoke(_settings(), headers=[(b"authorization", b"Bearer " + token)])

        assert resp.status == 401
        detail = resp.json["detail"]
        assert "Missing required claim" in detail or "Issuer" in detail


class TestHealthzBypass:
    @pytest.mark.asyncio
    async def test_get_healthz_bypasses_auth(self) -> None:
        # No Authorization header at all, but GET /healthz should still pass.
        resp = await _invoke(
            _settings(),
            scope_overrides={"method": "GET", "path": "/healthz"},
            headers=[],
        )

        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_post_healthz_still_requires_auth(self) -> None:
        # Only GET is exempt; POST /healthz should still require auth.
        resp = await _invoke(
            _settings(),
            scope_overrides={"method": "POST", "path": "/healthz"},
            headers=[],
        )

        assert resp.status == 401


class TestNonHttpScope:
    @pytest.mark.asyncio
    async def test_websocket_passes_through(self) -> None:
        resp = await _invoke(
            _settings(),
            scope_overrides={"type": "websocket", "path": "/ws"},
            headers=[],
        )

        # The inner app returns 200 for any non-http scope.
        assert resp.status == 200


class TestAuthDisabled:
    @pytest.mark.asyncio
    async def test_passthrough_when_disabled(self) -> None:
        # No Authorization header, but auth is disabled so it should pass through.
        resp = await _invoke(_settings(enabled=False), headers=[])

        assert resp.status == 200
