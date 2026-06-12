"""Shared fixtures for mtls tests -- generates X.509 certs via cryptography."""

from __future__ import annotations

import datetime
import ipaddress
import socket
import ssl
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _generate_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write_key(key: rsa.RSAPrivateKey, path: Path) -> Path:
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return path


def _write_cert(cert: x509.Certificate, path: Path) -> Path:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return path


def _make_ca(
    key: rsa.RSAPrivateKey,
    cn: str = "Test Root CA",
) -> x509.Certificate:
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
    )
    builder = builder.add_extension(
        x509.BasicConstraints(ca=True, path_length=None),
        critical=True,
    )
    return builder.sign(key, hashes.SHA256())


def _make_leaf(
    key: rsa.RSAPrivateKey,
    ca_key: rsa.RSAPrivateKey,
    ca_cert: x509.Certificate,
    cn: str = "server",
    not_before: datetime.datetime | None = None,
    not_after: datetime.datetime | None = None,
) -> x509.Certificate:
    now = datetime.datetime.now(datetime.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before or (now - datetime.timedelta(days=1)))
        .not_valid_after(not_after or (now + datetime.timedelta(days=90)))
    )
    builder = builder.add_extension(
        x509.BasicConstraints(ca=False, path_length=None),
        critical=True,
    )
    san = x509.SubjectAlternativeName(
        [
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]
    )
    builder = builder.add_extension(san, critical=False)
    return builder.sign(ca_key, hashes.SHA256())


@dataclass(frozen=True)
class CertPair:
    """Holds paths to a cert, key, and CA bundle on disk."""

    cert_path: str
    key_path: str
    ca_path: str


@pytest.fixture(scope="session")
def cert_authority(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate, Path]:
    """Session-scoped root CA key + cert. Returns (key, cert, ca_dir)."""
    ca_dir = tmp_path_factory.mktemp("ca")
    key = _generate_key()
    cert = _make_ca(key)
    _write_key(key, ca_dir / "ca.key")
    _write_cert(cert, ca_dir / "ca.crt")
    return key, cert, ca_dir


@pytest.fixture()
def mtls_cert_pair(
    cert_authority: tuple[rsa.RSAPrivateKey, x509.Certificate, Path],
    tmp_path: Path,
) -> CertPair:
    """Valid cert pair signed by the trusted session CA."""
    ca_key, ca_cert, ca_dir = cert_authority
    key = _generate_key()
    cert = _make_leaf(key, ca_key, ca_cert, cn="server")
    return CertPair(
        cert_path=str(_write_cert(cert, tmp_path / "server.crt")),
        key_path=str(_write_key(key, tmp_path / "server.key")),
        ca_path=str(ca_dir / "ca.crt"),
    )


@pytest.fixture()
def expired_cert_pair(
    cert_authority: tuple[rsa.RSAPrivateKey, x509.Certificate, Path],
    tmp_path: Path,
) -> CertPair:
    """Cert pair that has already expired."""
    ca_key, ca_cert, ca_dir = cert_authority
    key = _generate_key()
    now = datetime.datetime.now(datetime.UTC)
    cert = _make_leaf(
        key,
        ca_key,
        ca_cert,
        cn="expired",
        not_before=now - datetime.timedelta(days=365),
        not_after=now - datetime.timedelta(days=1),
    )
    return CertPair(
        cert_path=str(_write_cert(cert, tmp_path / "expired.crt")),
        key_path=str(_write_key(key, tmp_path / "expired.key")),
        ca_path=str(ca_dir / "ca.crt"),
    )


@pytest.fixture(scope="session")
def untrusted_ca(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate, Path]:
    """A second CA that is NOT in the trusted bundle."""
    ca_dir = tmp_path_factory.mktemp("untrusted_ca")
    key = _generate_key()
    cert = _make_ca(key, cn="Untrusted CA")
    _write_key(key, ca_dir / "untrusted-ca.key")
    _write_cert(cert, ca_dir / "untrusted-ca.crt")
    return key, cert, ca_dir


@pytest.fixture()
def untrusted_cert_pair(
    untrusted_ca: tuple[rsa.RSAPrivateKey, x509.Certificate, Path],
    tmp_path: Path,
) -> CertPair:
    """Cert pair signed by the untrusted CA."""
    ca_key, ca_cert, ca_dir = untrusted_ca
    key = _generate_key()
    cert = _make_leaf(key, ca_key, ca_cert, cn="untrusted-client")
    return CertPair(
        cert_path=str(_write_cert(cert, tmp_path / "untrusted.crt")),
        key_path=str(_write_key(key, tmp_path / "untrusted.key")),
        ca_path=str(ca_dir / "untrusted-ca.crt"),
    )


# ---------------------------------------------------------------------------
# Helpers for TLS handshake tests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TlsHandshakeResult:
    """Outcome of a TLS handshake attempt."""

    ok: bool
    error: Exception | None = None


def _tls_handshake(
    server_ctx: ssl.SSLContext,
    client_ctx: ssl.SSLContext,
) -> TlsHandshakeResult:
    """Perform a real TLS handshake between *server_ctx* and *client_ctx*.

    Binds to localhost on a random port, connects the client, and returns
    whether the mutual handshake succeeded.
    """
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]

    result: TlsHandshakeResult = TlsHandshakeResult(ok=False)
    barrier = threading.Barrier(2, timeout=5)

    def _server() -> None:
        try:
            conn, _ = server_sock.accept()
            with conn:
                tls = server_ctx.wrap_socket(conn, server_side=True)
                tls.recv(1)
                tls.send(b"OK")
                tls.close()
        except Exception as exc:
            nonlocal result
            result = TlsHandshakeResult(ok=False, error=exc)
        finally:
            barrier.wait(timeout=5)

    t = threading.Thread(target=_server, daemon=True)
    t.start()

    try:
        barrier.wait(timeout=5)
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with client_sock:
            tls = client_ctx.wrap_socket(client_sock, server_hostname="localhost")
            tls.connect(("127.0.0.1", port))
            tls.send(b"P")
            resp = tls.recv(2)
            result = TlsHandshakeResult(ok=(resp == b"OK"))
    except Exception as exc:
        result = TlsHandshakeResult(ok=False, error=exc)
    finally:
        barrier.wait(timeout=5)
        t.join(timeout=5)
        server_sock.close()

    return result
