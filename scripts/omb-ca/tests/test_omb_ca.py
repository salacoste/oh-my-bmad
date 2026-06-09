"""Unit tests for scripts/omb-ca (Story 56-2: omb-ca CLI tool).

Covers:
  - init creates CA cert + key files.
  - issue creates service cert + key files.
  - Issued cert is signed by the CA.
  - Issued cert has correct SAN (Docker-style hostname).
  - issue is idempotent (overwrites existing cert).
  - rotate replaces old cert with new one.
  - check returns 0 for valid certs.
  - check returns 1 for expired certs.
  - _docker_san_name prepends/appends correctly.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

OMB_CA_DIR = Path(__file__).resolve().parent.parent


def _load_module() -> object:
    """Import omb_ca from the scripts/omb-ca directory."""
    mod_name = "omb_ca"
    # Always reload to get fresh state
    init_path = OMB_CA_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        mod_name, init_path, submodule_search_locations=[str(OMB_CA_DIR)],
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# SAN name helper
# ---------------------------------------------------------------------------


class TestDockerSanName:
    """Tests for _docker_san_name."""

    def test_bare_name(self) -> None:
        mod = _load_module()
        assert mod._docker_san_name("task-registry") == "omb-task-registry-mcp"

    def test_already_prefixed(self) -> None:
        mod = _load_module()
        assert mod._docker_san_name("omb-worker") == "omb-worker-mcp"

    def test_already_suffixed(self) -> None:
        mod = _load_module()
        assert mod._docker_san_name("orchestrator-mcp") == "omb-orchestrator-mcp"

    def test_fully_qualified(self) -> None:
        mod = _load_module()
        assert mod._docker_san_name("omb-foo-mcp") == "omb-foo-mcp"


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


class TestInit:
    """Tests for init_ca."""

    def test_creates_ca_files(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)

        ca_cert = tmp_path / "ca.pem"
        ca_key = tmp_path / "ca-key.pem"
        assert ca_cert.is_file()
        assert ca_key.is_file()

    def test_ca_cert_is_valid(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)

        cert = x509.load_pem_x509_certificate((tmp_path / "ca.pem").read_bytes())
        assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "oh-my-bmad CA"
        assert cert.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value == "oh-my-bmad"

        # CA=True
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        assert bc.value.ca is True

    def test_ca_key_is_valid_rsa(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)

        key = serialization.load_pem_private_key(
            (tmp_path / "ca-key.pem").read_bytes(), password=None,
        )
        assert isinstance(key, rsa.RSAPrivateKey)
        assert key.key_size == 4096

    def test_idempotent_overwrites(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)
        cert1 = (tmp_path / "ca.pem").read_bytes()

        mod.init_ca(tmp_path)
        cert2 = (tmp_path / "ca.pem").read_bytes()

        # Different serial numbers prove it was regenerated
        c1 = x509.load_pem_x509_certificate(cert1)
        c2 = x509.load_pem_x509_certificate(cert2)
        assert c1.serial_number != c2.serial_number


# ---------------------------------------------------------------------------
# issue
# ---------------------------------------------------------------------------


class TestIssue:
    """Tests for issue_cert."""

    def test_creates_service_files(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)
        mod.issue_cert(tmp_path, "task-registry")

        assert (tmp_path / "task-registry.pem").is_file()
        assert (tmp_path / "task-registry-key.pem").is_file()

    def test_cert_signed_by_ca(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)
        mod.issue_cert(tmp_path, "my-svc")

        ca_cert = x509.load_pem_x509_certificate((tmp_path / "ca.pem").read_bytes())
        svc_cert = x509.load_pem_x509_certificate((tmp_path / "my-svc.pem").read_bytes())

        # Issuer must match CA subject
        assert svc_cert.issuer == ca_cert.subject

        # Verify signature
        ca_cert.public_key().verify(
            svc_cert.signature,
            svc_cert.tbs_certificate_bytes,
            padding.PKCS1v15(),
            svc_cert.signature_hash_algorithm,
        )

    def test_cert_has_correct_san(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)
        mod.issue_cert(tmp_path, "task-registry")

        cert = x509.load_pem_x509_certificate((tmp_path / "task-registry.pem").read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        dns_names = san.value.get_values_for_type(x509.DNSName)
        assert "omb-task-registry-mcp" in dns_names

    def test_cert_has_correct_cn(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)
        mod.issue_cert(tmp_path, "worker")

        cert = x509.load_pem_x509_certificate((tmp_path / "worker.pem").read_bytes())
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        assert cn[0].value == "worker"

    def test_cert_has_server_and_client_auth(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)
        mod.issue_cert(tmp_path, "svc")

        cert = x509.load_pem_x509_certificate((tmp_path / "svc.pem").read_bytes())
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        oids = [u.dotted_string for u in eku.value]
        assert ExtendedKeyUsageOID.SERVER_AUTH.dotted_string in oids
        assert ExtendedKeyUsageOID.CLIENT_AUTH.dotted_string in oids

    def test_cert_validity_72h(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)
        mod.issue_cert(tmp_path, "svc")

        cert = x509.load_pem_x509_certificate((tmp_path / "svc.pem").read_bytes())
        delta = cert.not_valid_after_utc - cert.not_valid_before_utc
        assert abs(delta.total_seconds() - 72 * 3600) < 5

    def test_service_key_is_rsa_2048(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)
        mod.issue_cert(tmp_path, "svc")

        key = serialization.load_pem_private_key(
            (tmp_path / "svc-key.pem").read_bytes(), password=None,
        )
        assert isinstance(key, rsa.RSAPrivateKey)
        assert key.key_size == 2048

    def test_idempotent_overwrites(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)
        mod.issue_cert(tmp_path, "svc")
        cert1 = (tmp_path / "svc.pem").read_bytes()

        mod.issue_cert(tmp_path, "svc")
        cert2 = (tmp_path / "svc.pem").read_bytes()

        c1 = x509.load_pem_x509_certificate(cert1)
        c2 = x509.load_pem_x509_certificate(cert2)
        assert c1.serial_number != c2.serial_number


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------


class TestRotate:
    """Tests for rotate_cert."""

    def test_replaces_old_cert(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)
        mod.issue_cert(tmp_path, "svc")
        old_serial = x509.load_pem_x509_certificate(
            (tmp_path / "svc.pem").read_bytes(),
        ).serial_number

        mod.rotate_cert(tmp_path, "svc")

        new_serial = x509.load_pem_x509_certificate(
            (tmp_path / "svc.pem").read_bytes(),
        ).serial_number
        assert new_serial != old_serial
        # No .new files left behind
        assert not (tmp_path / "svc.new.pem").exists()
        assert not (tmp_path / "svc.new-key.pem").exists()


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


class TestCheck:
    """Tests for check_certs."""

    def test_valid_certs_returns_0(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)
        mod.issue_cert(tmp_path, "svc")

        assert mod.check_certs(tmp_path) == 0

    def test_empty_dir_returns_0(self, tmp_path: Path) -> None:
        mod = _load_module()
        assert mod.check_certs(tmp_path) == 0

    def test_expired_cert_returns_1(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)

        # Create an expired cert manually
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_cert = x509.load_pem_x509_certificate((tmp_path / "ca.pem").read_bytes())
        ca_key = serialization.load_pem_private_key(
            (tmp_path / "ca-key.pem").read_bytes(), password=None,
        )
        now = datetime.now(UTC)
        expired_cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expired")]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(hours=2))
            .not_valid_after(now - timedelta(hours=1))
            .sign(ca_key, hashes.SHA256())
        )
        (tmp_path / "expired.pem").write_bytes(expired_cert.public_bytes(serialization.Encoding.PEM))

        assert mod.check_certs(tmp_path) == 1

    def test_invalid_pem_returns_1(self, tmp_path: Path) -> None:
        mod = _load_module()
        mod.init_ca(tmp_path)

        (tmp_path / "garbage.pem").write_text("not a certificate")
        assert mod.check_certs(tmp_path) == 1
