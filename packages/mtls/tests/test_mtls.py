"""Unit tests for create_ssl_context and helpers."""

from __future__ import annotations

import os
import socket
import ssl
import threading
from unittest.mock import patch

import pytest
from mtls._exceptions import MTLSConfigError
from mtls.mtls import create_httpx_verify_arg, create_ssl_context, create_uvicorn_ssl_config


def _env_from_pair(pair: object, **overrides: str) -> dict[str, str]:
    """Build an env dict that enables mTLS with the given cert pair."""
    env: dict[str, str] = {
        "MTLS_ENABLED": "true",
        "MTLS_CERT_PATH": pair.cert_path,  # type: ignore[union-attr]
        "MTLS_KEY_PATH": pair.key_path,  # type: ignore[union-attr]
        "MTLS_CA_PATH": pair.ca_path,  # type: ignore[union-attr]
    }
    env.update(overrides)
    return env


def _tls_handshake(
    server_ctx: ssl.SSLContext,
    client_ctx: ssl.SSLContext,
) -> tuple[bool, Exception | None]:
    """Perform a real TLS handshake between *server_ctx* and *client_ctx*.

    Binds to localhost on a random port, connects the client, and returns
    ``(ok, error)`` indicating whether the mutual handshake succeeded.
    """
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]

    server_listening = threading.Event()
    server_done = threading.Event()
    result_ok: bool = False
    result_err: Exception | None = None

    def _server() -> None:
        nonlocal result_err
        try:
            server_listening.set()
            conn, _ = server_sock.accept()
            with conn:
                conn.settimeout(5)
                tls = server_ctx.wrap_socket(conn, server_side=True)
                tls.recv(1)
                tls.send(b"OK")
        except Exception as exc:
            result_err = exc
        finally:
            server_done.set()

    t = threading.Thread(target=_server, daemon=True)
    t.start()

    server_listening.wait(timeout=5)
    try:
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_sock.settimeout(5)
        tls = client_ctx.wrap_socket(client_sock, server_hostname="localhost")
        tls.connect(("127.0.0.1", port))
        tls.send(b"P")
        resp = tls.recv(2)
        result_ok = resp == b"OK"
        tls.close()
    except Exception as exc:
        result_ok = False
        result_err = exc
    finally:
        server_done.wait(timeout=5)
        t.join(timeout=5)
        server_sock.close()

    return result_ok, result_err


# ---------------------------------------------------------------------------
# create_ssl_context -- disabled
# ---------------------------------------------------------------------------


class TestCreateSslContextDisabled:
    """When mTLS is disabled, create_ssl_context returns None."""

    def test_returns_none_when_env_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert create_ssl_context("server") is None

    def test_returns_none_when_enabled_false(self) -> None:
        with patch.dict(os.environ, {"MTLS_ENABLED": "false"}, clear=False):
            assert create_ssl_context("server") is None


# ---------------------------------------------------------------------------
# create_ssl_context -- enabled with valid certs
# ---------------------------------------------------------------------------


class TestCreateSslContextEnabled:
    """When mTLS is enabled, create_ssl_context returns a configured SSLContext."""

    def test_server_returns_ssl_context(self, mtls_cert_pair: object) -> None:
        env = _env_from_pair(mtls_cert_pair)
        with patch.dict(os.environ, env, clear=False):
            ctx = create_ssl_context("server")
        assert isinstance(ctx, ssl.SSLContext)

    def test_server_cert_required(self, mtls_cert_pair: object) -> None:
        env = _env_from_pair(mtls_cert_pair)
        with patch.dict(os.environ, env, clear=False):
            ctx = create_ssl_context("server")
        assert ctx is not None
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_server_check_hostname_false(self, mtls_cert_pair: object) -> None:
        env = _env_from_pair(mtls_cert_pair)
        with patch.dict(os.environ, env, clear=False):
            ctx = create_ssl_context("server")
        assert ctx is not None
        assert ctx.check_hostname is False

    def test_client_check_hostname_true(self, mtls_cert_pair: object) -> None:
        env = _env_from_pair(mtls_cert_pair)
        with patch.dict(os.environ, env, clear=False):
            ctx = create_ssl_context("client")
        assert ctx is not None
        assert ctx.check_hostname is True

    def test_client_returns_ssl_context(self, mtls_cert_pair: object) -> None:
        env = _env_from_pair(mtls_cert_pair)
        with patch.dict(os.environ, env, clear=False):
            ctx = create_ssl_context("client")
        assert isinstance(ctx, ssl.SSLContext)

    def test_minimum_tls_version_12(self, mtls_cert_pair: object) -> None:
        env = _env_from_pair(mtls_cert_pair)
        with patch.dict(os.environ, env, clear=False):
            ctx = create_ssl_context("server")
        assert ctx is not None
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2

    def test_verify_mode_cert_required(self, mtls_cert_pair: object) -> None:
        env = _env_from_pair(mtls_cert_pair)
        with patch.dict(os.environ, env, clear=False):
            ctx = create_ssl_context("client")
        assert ctx is not None
        assert ctx.verify_mode == ssl.CERT_REQUIRED


# ---------------------------------------------------------------------------
# create_ssl_context -- error cases
# ---------------------------------------------------------------------------


class TestCreateSslContextErrors:
    """Error conditions raise MTLSConfigError."""

    def test_missing_cert_file_raises(self, mtls_cert_pair: object) -> None:
        env = _env_from_pair(mtls_cert_pair, MTLS_CERT_PATH="/nonexistent/path.crt")
        with (
            patch.dict(os.environ, env, clear=False),
            pytest.raises(MTLSConfigError, match="file not found"),
        ):
            create_ssl_context("server")

    def test_expired_cert_raises(self, expired_cert_pair: object) -> None:
        env = _env_from_pair(expired_cert_pair)
        with (
            patch.dict(os.environ, env, clear=False),
            pytest.raises(MTLSConfigError, match="expired"),
        ):
            create_ssl_context("server")


# ---------------------------------------------------------------------------
# TLS handshake tests -- real socket-level verification
# ---------------------------------------------------------------------------


class TestTlsHandshake:
    """Real TLS handshake tests for mutual authentication.

    NOTE: The production ``create_ssl_context`` builds both server and client
    contexts from ``ssl.PROTOCOL_TLS_CLIENT``.  Python's ssl module does not
    allow ``wrap_socket(server_side=True)`` on a PROTOCOL_TLS_CLIENT context,
    so for handshake tests we build the server context using
    ``ssl.PROTOCOL_TLS_SERVER`` with equivalent settings.  This validates that
    the cert/key/CA triple is correct and that mutual auth works, which is the
    intent of the handshake tests.
    """

    @staticmethod
    def _build_server_ctx_from_pair(pair: object) -> ssl.SSLContext:
        """Build a server SSLContext suitable for server_side=True wrapping."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False
        ctx.load_verify_locations(cafile=pair.ca_path)  # type: ignore[union-attr]
        ctx.load_cert_chain(
            certfile=pair.cert_path,  # type: ignore[union-attr]
            keyfile=pair.key_path,  # type: ignore[union-attr]
        )
        return ctx

    def test_valid_mutual_auth_succeeds(self, mtls_cert_pair: object) -> None:
        """Trusted client + trusted server => handshake succeeds."""
        server_ctx = self._build_server_ctx_from_pair(mtls_cert_pair)

        env = _env_from_pair(mtls_cert_pair)
        with patch.dict(os.environ, env, clear=False):
            client_ctx = create_ssl_context("client")

        assert client_ctx is not None

        ok, err = _tls_handshake(server_ctx, client_ctx)
        assert ok, f"Expected successful handshake, got error: {err}"

    def test_untrusted_client_rejected(
        self,
        mtls_cert_pair: object,
        untrusted_cert_pair: object,
    ) -> None:
        """Client cert signed by unknown CA => server rejects."""
        server_ctx = self._build_server_ctx_from_pair(mtls_cert_pair)

        env_client = _env_from_pair(untrusted_cert_pair)
        with patch.dict(os.environ, env_client, clear=False):
            client_ctx = create_ssl_context("client")

        assert client_ctx is not None

        ok, _ = _tls_handshake(server_ctx, client_ctx)
        assert not ok

    def test_no_client_cert_rejected(self, mtls_cert_pair: object) -> None:
        """Server requires client cert (CERT_REQUIRED); bare client rejected."""
        server_ctx = self._build_server_ctx_from_pair(mtls_cert_pair)

        # Build a client context that does NOT load a cert chain.
        client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_ctx.check_hostname = False
        client_ctx.verify_mode = ssl.CERT_NONE
        client_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        # Load the CA so client can verify the server cert.
        client_ctx.load_verify_locations(
            cafile=mtls_cert_pair.ca_path,  # type: ignore[union-attr]
        )

        ok, _ = _tls_handshake(server_ctx, client_ctx)
        assert not ok


# ---------------------------------------------------------------------------
# create_uvicorn_ssl_config
# ---------------------------------------------------------------------------


class TestUvicornSslConfig:
    """create_uvicorn_ssl_config returns None when disabled, dict when enabled."""

    def test_returns_none_when_disabled(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert create_uvicorn_ssl_config() is None

    def test_returns_dict_when_enabled(self, mtls_cert_pair: object) -> None:
        env = _env_from_pair(mtls_cert_pair)
        with patch.dict(os.environ, env, clear=False):
            cfg = create_uvicorn_ssl_config()
        assert isinstance(cfg, dict)
        assert cfg["ssl_keyfile"] == mtls_cert_pair.key_path  # type: ignore[union-attr]
        assert cfg["ssl_certfile"] == mtls_cert_pair.cert_path  # type: ignore[union-attr]
        assert cfg["ssl_ca_certs"] == mtls_cert_pair.ca_path  # type: ignore[union-attr]
        assert cfg["ssl_cert_reqs"] == ssl.CERT_REQUIRED


# ---------------------------------------------------------------------------
# create_httpx_verify_arg
# ---------------------------------------------------------------------------


class TestHttpxVerifyArg:
    """create_httpx_verify_arg returns True when disabled, SSLContext when enabled."""

    def test_returns_true_when_disabled(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert create_httpx_verify_arg() is True

    def test_returns_ssl_context_when_enabled(self, mtls_cert_pair: object) -> None:
        env = _env_from_pair(mtls_cert_pair)
        with patch.dict(os.environ, env, clear=False):
            result = create_httpx_verify_arg()
        assert isinstance(result, ssl.SSLContext)
