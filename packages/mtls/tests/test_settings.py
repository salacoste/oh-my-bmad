"""Unit tests for MTLSSettings."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from mtls._exceptions import MTLSConfigError
from mtls.settings import MTLSSettings


class TestFromEnvDisabled:
    """MTLSSettings.from_env() when mTLS is not configured."""

    def test_no_env_vars_returns_disabled(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = MTLSSettings.from_env()
        assert settings.enabled is False

    def test_enabled_unset_returns_disabled(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "MTLS_ENABLED"}
        with patch.dict(os.environ, env, clear=True):
            settings = MTLSSettings.from_env()
        assert settings.enabled is False

    def test_paths_default_to_none_when_disabled(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = MTLSSettings.from_env()
        assert settings.cert_path is None
        assert settings.key_path is None
        assert settings.ca_path is None


class TestFromEnvEnabled:
    """MTLSSettings.from_env() when MTLS_ENABLED is set."""

    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "Yes", "YES"])
    def test_truthy_values_enable(self, value: str) -> None:
        env = {"MTLS_ENABLED": value}
        with patch.dict(os.environ, env, clear=False), pytest.raises(MTLSConfigError):
            MTLSSettings.from_env()

    @pytest.mark.parametrize("value", ["false", "False", "0", "", "no", "maybe", "random"])
    def test_non_truthy_values_keep_disabled(self, value: str) -> None:
        env = {"MTLS_ENABLED": value}
        with patch.dict(os.environ, env, clear=False):
            settings = MTLSSettings.from_env()
        assert settings.enabled is False

    def test_enabled_true_with_missing_cert_path_raises(self) -> None:
        env = {
            "MTLS_ENABLED": "true",
            "MTLS_KEY_PATH": "/some/key",
            "MTLS_CA_PATH": "/some/ca",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            pytest.raises(MTLSConfigError, match="MTLS_CERT_PATH"),
        ):
            MTLSSettings.from_env()

    def test_enabled_true_with_missing_key_path_raises(self) -> None:
        env = {
            "MTLS_ENABLED": "true",
            "MTLS_CERT_PATH": "/some/cert",
            "MTLS_CA_PATH": "/some/ca",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            pytest.raises(MTLSConfigError, match="MTLS_KEY_PATH"),
        ):
            MTLSSettings.from_env()

    def test_enabled_true_with_missing_ca_path_raises(self) -> None:
        env = {
            "MTLS_ENABLED": "true",
            "MTLS_CERT_PATH": "/some/cert",
            "MTLS_KEY_PATH": "/some/key",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            pytest.raises(MTLSConfigError, match="MTLS_CA_PATH"),
        ):
            MTLSSettings.from_env()

    def test_enabled_true_with_all_paths_validates(self) -> None:
        env = {
            "MTLS_ENABLED": "true",
            "MTLS_CERT_PATH": "/some/cert",
            "MTLS_KEY_PATH": "/some/key",
            "MTLS_CA_PATH": "/some/ca",
        }
        with patch.dict(os.environ, env, clear=False):
            settings = MTLSSettings.from_env()
        assert settings.enabled is True
        assert settings.cert_path == "/some/cert"
        assert settings.key_path == "/some/key"
        assert settings.ca_path == "/some/ca"

    def test_enabled_true_with_empty_string_paths_raises(self) -> None:
        env = {
            "MTLS_ENABLED": "true",
            "MTLS_CERT_PATH": "  ",
            "MTLS_KEY_PATH": "/some/key",
            "MTLS_CA_PATH": "/some/ca",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            pytest.raises(MTLSConfigError, match="MTLS_CERT_PATH"),
        ):
            MTLSSettings.from_env()

    def test_rotation_warning_hours_custom(self) -> None:
        env = {
            "MTLS_ENABLED": "false",
            "MTLS_ROTATION_WARNING_HOURS": "48",
        }
        with patch.dict(os.environ, env, clear=False):
            settings = MTLSSettings.from_env()
        assert settings.rotation_warning_hours == 48

    def test_rotation_warning_hours_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = MTLSSettings.from_env()
        assert settings.rotation_warning_hours == 24
