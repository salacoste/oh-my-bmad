"""Unit tests for McpAuthSettings."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from mcp_auth.settings import McpAuthSettings
from pydantic import SecretStr, ValidationError

# A 64-char hex string (32 bytes) — mimics `openssl rand -hex 32`.
_VALID_KEY = "a" * 64
_SHORT_KEY = "tooshort"


class TestFromEnv:
    """McpAuthSettings.from_env() behaviour."""

    def test_no_jwt_secret_key_means_disabled(self) -> None:
        env = {"HOME": "/tmp", "PATH": "/usr/bin"}
        with patch.dict(os.environ, env, clear=True):
            settings = McpAuthSettings.from_env()
        assert settings.jwt_secret_key is None
        assert settings.enabled is False

    def test_valid_key_means_enabled(self) -> None:
        with patch.dict(os.environ, {"JWT_SECRET_KEY": _VALID_KEY}, clear=False):
            settings = McpAuthSettings.from_env()
        assert settings.jwt_secret_key is not None
        assert settings.enabled is True

    def test_empty_string_means_disabled(self) -> None:
        with patch.dict(os.environ, {"JWT_SECRET_KEY": ""}, clear=False):
            settings = McpAuthSettings.from_env()
        assert settings.jwt_secret_key is None
        assert settings.enabled is False

    def test_whitespace_only_means_disabled(self) -> None:
        with patch.dict(os.environ, {"JWT_SECRET_KEY": "   \t\n  "}, clear=False):
            settings = McpAuthSettings.from_env()
        assert settings.jwt_secret_key is None
        assert settings.enabled is False

    def test_short_key_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError, match="at least 32 BYTES"):
            McpAuthSettings(jwt_secret_key=SecretStr(_SHORT_KEY))


class TestDefaults:
    """Field defaults match the spec."""

    def _make(self) -> McpAuthSettings:
        return McpAuthSettings(jwt_secret_key=SecretStr(_VALID_KEY))

    def test_algorithm_default(self) -> None:
        assert self._make().algorithm == "HS256"

    def test_issuer_default(self) -> None:
        assert self._make().issuer == "oh-my-bmad/registry-api"

    def test_leeway_seconds_default(self) -> None:
        assert self._make().leeway_seconds == 30


class TestSecretStrWrapping:
    """jwt_secret_key is never leaked via repr / model_dump."""

    def test_repr_does_not_leak(self) -> None:
        settings = McpAuthSettings(jwt_secret_key=SecretStr(_VALID_KEY))
        r = repr(settings)
        assert _VALID_KEY not in r

    def test_model_dump_masks_value(self) -> None:
        settings = McpAuthSettings(jwt_secret_key=SecretStr(_VALID_KEY))
        dumped = settings.model_dump()
        raw = dumped["jwt_secret_key"]
        # SecretStr serialises to '**********' by default.
        assert _VALID_KEY not in str(raw)
