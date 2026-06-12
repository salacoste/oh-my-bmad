"""omb-ca — mTLS certificate management CLI for oh-my-bmad.

Subcommands
-----------
  init                Generate root CA key + self-signed certificate.
  issue <name>        Generate per-service certificate signed by the CA.
  rotate <name>       Issue a new cert alongside the old one (.new suffix).
  check               Validate all certificates in the cert directory.

All output goes to ``./certs/`` relative to *certs_dir* (default: cwd).
Uses only stdlib + ``cryptography`` — no other external dependencies.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CA_KEY_SIZE = 4096
SVC_KEY_SIZE = 2048
CA_VALIDITY_DAYS = 365
SVC_VALIDITY_HOURS = 72
WARN_HOURS = 24

CA_CERT_NAME = "ca.pem"
CA_KEY_NAME = "ca-key.pem"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_dir(certs_dir: Path) -> Path:
    """Return resolved certs directory, creating it if necessary."""
    certs_dir.mkdir(parents=True, exist_ok=True)
    return certs_dir


def _docker_san_name(service_name: str) -> str:
    """Build a Docker-style SAN hostname from *service_name*.

    Prepends ``omb-`` and appends ``-mcp`` when not already present so that
    bare names like ``task-registry`` map to ``omb-task-registry-mcp``.
    """
    name = service_name
    if not name.startswith("omb-"):
        name = f"omb-{name}"
    if not name.endswith("-mcp"):
        name = f"{name}-mcp"
    return name


def _load_ca(
    certs_dir: Path,
) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """Load the CA certificate and private key from *certs_dir*."""
    ca_cert_path = certs_dir / CA_CERT_NAME
    ca_key_path = certs_dir / CA_KEY_NAME

    if not ca_cert_path.is_file():
        print(f"error: CA certificate not found: {ca_cert_path}", file=sys.stderr)
        raise SystemExit(1)
    if not ca_key_path.is_file():
        print(f"error: CA private key not found: {ca_key_path}", file=sys.stderr)
        raise SystemExit(1)

    ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
    ca_key = serialization.load_pem_private_key(
        ca_key_path.read_bytes(),
        password=None,
    )
    assert isinstance(ca_key, rsa.RSAPrivateKey)
    return ca_cert, ca_key


# ---------------------------------------------------------------------------
# Core operations — importable for testing
# ---------------------------------------------------------------------------


def init_ca(certs_dir: Path) -> None:
    """Generate a root CA key and self-signed certificate."""
    certs_dir = _ensure_dir(certs_dir)
    ca_key_path = certs_dir / CA_KEY_NAME
    ca_cert_path = certs_dir / CA_CERT_NAME

    print("generating root CA key (RSA 4096) ...", file=sys.stderr)
    key = rsa.generate_private_key(public_exponent=65537, key_size=CA_KEY_SIZE)

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "oh-my-bmad CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "oh-my-bmad"),
        ]
    )

    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=CA_VALIDITY_DAYS))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                key_encipherment=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    )

    cert = builder.sign(key, hashes.SHA256())

    ca_key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
    )
    ca_cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print(f"CA certificate: {ca_cert_path}", file=sys.stderr)
    print(f"CA private key: {ca_key_path}", file=sys.stderr)


def issue_cert(
    certs_dir: Path,
    service_name: str,
    *,
    suffix: str = "",
) -> None:
    """Generate a per-service certificate signed by the CA.

    Parameters
    ----------
    certs_dir:
        Directory containing ``ca.pem`` / ``ca-key.pem``; outputs go here too.
    service_name:
        Short name for the service (e.g. ``task-registry``).
    suffix:
        Optional filename suffix for output (used by ``rotate``).
    """
    certs_dir = _ensure_dir(certs_dir)
    ca_cert, ca_key = _load_ca(certs_dir)

    san_name = _docker_san_name(service_name)

    print(
        f"generating service key for {service_name} (RSA 2048) ...",
        file=sys.stderr,
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=SVC_KEY_SIZE)

    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, service_name),
        ]
    )

    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(hours=SVC_VALIDITY_HOURS))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(san_name)]),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [
                    ExtendedKeyUsageOID.SERVER_AUTH,
                    ExtendedKeyUsageOID.CLIENT_AUTH,
                ]
            ),
            critical=False,
        )
    )

    cert = builder.sign(ca_key, hashes.SHA256())

    cert_path = certs_dir / f"{service_name}{suffix}.pem"
    key_path = certs_dir / f"{service_name}{suffix}-key.pem"

    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
    )

    print(f"service certificate: {cert_path}", file=sys.stderr)
    print(f"service private key: {key_path}", file=sys.stderr)


def rotate_cert(certs_dir: Path, service_name: str) -> None:
    """Issue a new certificate with ``.new`` suffix, then replace the old one."""
    certs_dir = _ensure_dir(certs_dir)

    old_cert = certs_dir / f"{service_name}.pem"
    old_key = certs_dir / f"{service_name}-key.pem"

    # Issue with .new suffix first
    issue_cert(certs_dir, service_name, suffix=".new")

    new_cert = certs_dir / f"{service_name}.new.pem"
    new_key = certs_dir / f"{service_name}.new-key.pem"

    # Replace old files with new ones
    if old_cert.is_file():
        old_cert.unlink()
    if old_key.is_file():
        old_key.unlink()

    new_cert.rename(old_cert)
    new_key.rename(old_key)

    print(f"rotated certificate for {service_name}", file=sys.stderr)


def check_certs(certs_dir: Path) -> int:
    """Validate all certificates in *certs_dir*.

    Returns
    -------
    int
        0 if all certificates are valid, 1 if any are expired.
    """
    certs_dir = _ensure_dir(certs_dir)
    now = datetime.now(UTC)
    warn_threshold = timedelta(hours=WARN_HOURS)
    has_expired = False

    cert_files = sorted(certs_dir.glob("*.pem"))
    # Exclude private key files from cert checking
    cert_files = [f for f in cert_files if not f.name.endswith("-key.pem")]

    if not cert_files:
        print("no certificates found in", certs_dir, file=sys.stderr)
        return 0

    for cert_file in cert_files:
        try:
            cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
        except Exception as exc:
            print(f"  {cert_file.name}: INVALID - {exc}", file=sys.stderr)
            has_expired = True
            continue

        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        cn_str = cn[0].value if cn else "(no CN)"
        not_after = cert.not_valid_after_utc

        if now >= not_after:
            status = "EXPIRED"
            has_expired = True
        elif now + warn_threshold >= not_after:
            status = f"WARNING (expires {not_after.isoformat()})"
        else:
            status = f"valid (expires {not_after.isoformat()})"

        print(f"  {cert_file.name}: CN={cn_str}  {status}", file=sys.stderr)

    return 1 if has_expired else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="omb-ca",
        description="mTLS certificate management for oh-my-bmad",
    )
    parser.add_argument(
        "--certs-dir",
        type=Path,
        default=Path("./certs"),
        help="Directory for CA and service certificates (default: ./certs)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Generate root CA key + self-signed certificate")

    issue_p = sub.add_parser("issue", help="Generate per-service certificate")
    issue_p.add_argument("service_name", help="Service name (e.g. task-registry)")

    rotate_p = sub.add_parser("rotate", help="Rotate a service certificate")
    rotate_p.add_argument("service_name", help="Service name (e.g. task-registry)")

    sub.add_parser("check", help="Validate all certificates")

    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    certs_dir = args.certs_dir.resolve()

    if args.command == "init":
        init_ca(certs_dir)
    elif args.command == "issue":
        issue_cert(certs_dir, args.service_name)
    elif args.command == "rotate":
        rotate_cert(certs_dir, args.service_name)
    elif args.command == "check":
        rc = check_certs(certs_dir)
        raise SystemExit(rc)
