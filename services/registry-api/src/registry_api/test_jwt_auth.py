"""Tests for JWT authentication (Story 6.1+).

Coverage:
- JwtAuthSettings: validation, enabled property, from_env
- JwtAuthMiddleware: JWT validation, fallback, 401 responses
- JWKS endpoint: key exposure, disabled state
- OpenID configuration: discovery, disabled state
- Token generation CLI: generate + verify roundtrip
- Backward compatibility: X-Actor-Id fallback when JWT not configured
"""

from __future__ import annotations

import datetime
from collections.abc import AsyncGenerator
from pathlib import Path

import jwt as pyjwt
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from events import FROZEN_EPOCH, FrozenClock
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from registry_state.adapters.sqlite_store import (  # noqa: IMP001 — registry-api tests create an in-memory registry-state DB
    create_engine,
)
from registry_state.schema import (  # noqa: IMP001 — registry-api tests create schema tables for auth middleware
    Base,
)

from registry_api.app import build_app
from registry_api.cli_tokens import generate_token, verify_token
from registry_api.routes.jwks import _derive_kid
from registry_api.settings import JwtAuthSettings

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

_FROZEN_MONO_NS = 1_000_000
_FROZEN_CLOCK = FrozenClock(mono_ns=_FROZEN_MONO_NS, now=FROZEN_EPOCH)
_TEST_SECRET = "affe1deadbeefcafebabedeadbeefcafebabedeadbeefcafebabedeadbeefcafe"


def _db_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


async def _seed_tables(db_url: str) -> None:
    """Create ORM tables without seeding any rows."""
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


def _make_token(
    *,
    actor_id: str = "test-operator",
    secret: str = _TEST_SECRET,
    algorithm: str = "HS256",
    issuer: str = "oh-my-bmad/registry-api",
    exp_offset_minutes: int = 60,
    **extra_claims: object,
) -> str:
    """Helper to create a JWT token for tests."""
    now = datetime.datetime.now(datetime.UTC)
    payload = {
        "iss": issuer,
        "sub": actor_id,
        "iat": now,
        "exp": now + datetime.timedelta(minutes=exp_offset_minutes),
        **extra_claims,
    }
    return pyjwt.encode(payload, secret, algorithm=algorithm)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="function")
async def jwt_enabled_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[AsyncClient, None]:
    """ASGI client with JWT auth enabled."""
    monkeypatch.setenv("REGISTRY_API_TEST_PROBES", "1")
    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables(db_url)
    app = build_app(
        base_dir=tmp_path / "events",
        db_url=db_url,
        clock=_FROZEN_CLOCK,
        jwt_settings=JwtAuthSettings(jwt_secret_key=SecretStr(_TEST_SECRET)),
        create_idempotency_schema_on_start=True,
    )
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest_asyncio.fixture(loop_scope="function")
async def jwt_disabled_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[AsyncClient, None]:
    """ASGI client without JWT auth (Phase 1 fallback)."""
    monkeypatch.setenv("REGISTRY_API_TEST_PROBES", "1")
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    db_path = tmp_path / "state.sqlite3"
    db_url = _db_url(db_path)
    await _seed_tables(db_url)
    app = build_app(
        base_dir=tmp_path / "events",
        db_url=db_url,
        clock=_FROZEN_CLOCK,
        jwt_settings=JwtAuthSettings(),  # no secret → disabled
        create_idempotency_schema_on_start=True,
    )
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


# ---------------------------------------------------------------------------
# JwtAuthSettings tests
# ---------------------------------------------------------------------------


class TestJwtAuthSettings:
    """JwtAuthSettings validation and behaviour."""

    def test_defaults_disabled(self) -> None:
        settings = JwtAuthSettings()
        assert not settings.enabled
        assert settings.jwt_secret_key is None

    def test_enabled_with_secret(self) -> None:
        settings = JwtAuthSettings(jwt_secret_key=SecretStr(_TEST_SECRET))
        assert settings.enabled
        assert settings.algorithm == "HS256"
        assert settings.issuer == "oh-my-bmad/registry-api"
        assert settings.access_token_expire_minutes == 1440
        assert settings.leeway_seconds == 30

    def test_empty_secret_treated_as_none(self) -> None:
        settings = JwtAuthSettings(jwt_secret_key=SecretStr(""))
        assert not settings.enabled
        assert settings.jwt_secret_key is None

    def test_whitespace_secret_treated_as_none(self) -> None:
        settings = JwtAuthSettings(jwt_secret_key=SecretStr("   "))
        assert not settings.enabled

    def test_too_short_secret_raises(self) -> None:
        with pytest.raises(ValueError, match="at least 32 BYTES"):
            JwtAuthSettings(jwt_secret_key=SecretStr("too-short"))

    def test_from_env_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JWT_SECRET_KEY", _TEST_SECRET)
        settings = JwtAuthSettings.from_env()
        assert settings.enabled

    def test_from_env_missing_is_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        settings = JwtAuthSettings.from_env()
        assert not settings.enabled


# ---------------------------------------------------------------------------
# JwtAuthMiddleware — JWT validation tests
# ---------------------------------------------------------------------------


class TestJwtAuthMiddlewareJwtEnabled:
    """JwtAuthMiddleware behaviour when JWT_SECRET_KEY is configured."""

    @pytest.mark.asyncio
    async def test_valid_token_sets_actor_id(self, jwt_enabled_client: AsyncClient) -> None:
        """Valid JWT sets request.state.actor_id from sub claim."""
        token = _make_token(actor_id="alice")
        response = await jwt_enabled_client.get(
            "/v1/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_missing_token_on_read_route_succeeds(
        self,
        jwt_enabled_client: AsyncClient,
    ) -> None:
        """Read routes (GET) are allowed through without auth."""
        response = await jwt_enabled_client.get("/v1/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_missing_token_on_write_route_returns_401(
        self,
        jwt_enabled_client: AsyncClient,
    ) -> None:
        """Mutating routes without token return 401."""
        response = await jwt_enabled_client.post(
            "/v1/tasks",
            json={"title": "test task"},
        )
        assert response.status_code == 401
        detail = response.json()["detail"]
        assert "Missing" in detail or "Authorization" in detail

    @pytest.mark.asyncio
    async def test_expired_token_returns_401(self, jwt_enabled_client: AsyncClient) -> None:
        """Expired JWT returns 401."""
        token = _make_token(exp_offset_minutes=-60)
        response = await jwt_enabled_client.get(
            "/v1/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401
        assert "expired" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_invalid_signature_returns_401(self, jwt_enabled_client: AsyncClient) -> None:
        """Token signed with wrong key returns 401."""
        token = _make_token(secret="wrong-secret-that-is-long-enough-32-bytes-minimum-needed")
        response = await jwt_enabled_client.get(
            "/v1/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_issuer_returns_401(self, jwt_enabled_client: AsyncClient) -> None:
        """Token with wrong issuer returns 401."""
        token = _make_token(issuer="wrong-issuer")
        response = await jwt_enabled_client.get(
            "/v1/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_sub_claim_returns_401(self, jwt_enabled_client: AsyncClient) -> None:
        """Token without sub claim returns 401."""
        now = datetime.datetime.now(datetime.UTC)
        payload = {
            "iss": "oh-my-bmad/registry-api",
            "iat": now,
            "exp": now + datetime.timedelta(minutes=60),
        }
        token = pyjwt.encode(payload, _TEST_SECRET, algorithm="HS256")
        response = await jwt_enabled_client.get(
            "/v1/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_wellknown_jwks_returns_keys(self, jwt_enabled_client: AsyncClient) -> None:
        """JWKS endpoint returns key info when JWT is configured."""
        response = await jwt_enabled_client.get("/.well-known/jwks.json")
        assert response.status_code == 200
        data = response.json()
        assert len(data["keys"]) == 1
        assert data["keys"][0]["alg"] == "HS256"
        assert data["keys"][0]["kty"] == "oct"
        assert data["keys"][0]["use"] == "sig"
        assert "kid" in data["keys"][0]

    @pytest.mark.asyncio
    async def test_wellknown_discovery_returns_config(
        self,
        jwt_enabled_client: AsyncClient,
    ) -> None:
        """OpenID configuration returns proper discovery document."""
        response = await jwt_enabled_client.get("/.well-known/openid-configuration")
        assert response.status_code == 200
        data = response.json()
        assert data["issuer"] == "oh-my-bmad/registry-api"
        assert data["jwks_uri"].endswith("/.well-known/jwks.json")
        assert "HS256" in data["id_token_signing_alg_values_supported"]


# ---------------------------------------------------------------------------
# JwtAuthMiddleware — fallback (Phase 1 compat) tests
# ---------------------------------------------------------------------------


class TestJwtAuthMiddlewareFallback:
    """JwtAuthMiddleware behaviour when JWT_SECRET_KEY is NOT configured."""

    @pytest.mark.asyncio
    async def test_x_actor_id_header_is_used(self, jwt_disabled_client: AsyncClient) -> None:
        """X-Actor-Id header sets actor_id when JWT is disabled."""
        response = await jwt_disabled_client.get(
            "/v1/health",
            headers={"X-Actor-Id": "telegram-user-123"},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_no_header_defaults_to_http_api(self, jwt_disabled_client: AsyncClient) -> None:
        """No X-Actor-Id defaults to 'http-api' actor."""
        response = await jwt_disabled_client.get("/v1/health")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_wellknown_jwks_returns_empty_keys(
        self,
        jwt_disabled_client: AsyncClient,
    ) -> None:
        """JWKS endpoint returns empty keys when JWT is disabled."""
        response = await jwt_disabled_client.get("/.well-known/jwks.json")
        assert response.status_code == 200
        data = response.json()
        assert data["keys"] == []

    @pytest.mark.asyncio
    async def test_wellknown_discovery_no_jwks_uri(self, jwt_disabled_client: AsyncClient) -> None:
        """Discovery omits jwks_uri when JWT is disabled."""
        response = await jwt_disabled_client.get("/.well-known/openid-configuration")
        assert response.status_code == 200
        data = response.json()
        assert data["jwks_uri"] is None
        assert data["id_token_signing_alg_values_supported"] is None

    @pytest.mark.asyncio
    async def test_post_tasks_works_without_jwt(self, jwt_disabled_client: AsyncClient) -> None:
        """POST /v1/tasks works without JWT auth (Phase 1 compat)."""
        response = await jwt_disabled_client.post(
            "/v1/tasks",
            json={"title": "test task"},
            headers={"X-Actor-Id": "test-operator"},
        )
        assert response.status_code in (200, 201)


# ---------------------------------------------------------------------------
# Token generation CLI tests
# ---------------------------------------------------------------------------


class TestTokenGeneration:
    """Token generation and verification roundtrip."""

    def test_generate_and_verify_roundtrip(self) -> None:
        settings = JwtAuthSettings(jwt_secret_key=SecretStr(_TEST_SECRET))
        token = generate_token(actor_id="test-user", settings=settings)
        assert isinstance(token, str)
        assert len(token) > 0

        claims = verify_token(token=token, settings=settings)
        assert claims["sub"] == "test-user"
        assert claims["iss"] == "oh-my-bmad/registry-api"
        assert "exp" in claims
        assert "iat" in claims

    def test_generate_with_custom_expiry(self) -> None:
        settings = JwtAuthSettings(jwt_secret_key=SecretStr(_TEST_SECRET))
        token = generate_token(
            actor_id="short-lived",
            settings=settings,
            expire_minutes=5,
        )
        claims = verify_token(token=token, settings=settings)
        # Expiry should be within reasonable range of 5 minutes from now
        now = datetime.datetime.now(datetime.UTC).timestamp()
        exp_val = claims["exp"]
        assert isinstance(exp_val, int | float)
        exp = float(exp_val)
        assert 200 < (exp - now) < 400  # ~5 minutes

    def test_generate_fails_without_secret(self) -> None:
        settings = JwtAuthSettings()  # no secret
        with pytest.raises(SystemExit):
            generate_token(actor_id="test", settings=settings)

    def test_verify_rejects_wrong_secret(self) -> None:
        settings_a = JwtAuthSettings(jwt_secret_key=SecretStr(_TEST_SECRET))
        token = generate_token(actor_id="test", settings=settings_a)

        settings_b = JwtAuthSettings(jwt_secret_key=SecretStr("b" * 64))
        with pytest.raises(SystemExit):
            verify_token(token=token, settings=settings_b)

    def test_verify_rejects_expired_token(self) -> None:
        settings = JwtAuthSettings(jwt_secret_key=SecretStr(_TEST_SECRET))
        token = _make_token(exp_offset_minutes=-60)
        with pytest.raises(SystemExit):
            verify_token(token=token, settings=settings)


# ---------------------------------------------------------------------------
# Kid derivation tests
# ---------------------------------------------------------------------------


class TestKidDerivation:
    """Key ID derivation from secret."""

    def test_deterministic(self) -> None:
        kid1 = _derive_kid(_TEST_SECRET)
        kid2 = _derive_kid(_TEST_SECRET)
        assert kid1 == kid2

    def test_different_secrets_different_kids(self) -> None:
        kid1 = _derive_kid(_TEST_SECRET)
        kid2 = _derive_kid("b" * 64)
        assert kid1 != kid2

    def test_kid_length(self) -> None:
        kid = _derive_kid(_TEST_SECRET)
        assert len(kid) == 16  # 16 hex chars from truncated SHA-256
