"""Clean fixture: env-var-based cert paths — NO violation."""
import os

cert_path = os.environ.get("MTLS_CERT_PATH")
key_path = os.getenv("MTLS_KEY_PATH")
ca_path = os.environ.get("MTLS_CA_PATH", "/etc/ssl/certs/ca-certificates.crt")


def load_cert() -> str:
    return os.environ.get("SSL_CERT_FILE", "")


def load_custom() -> str:
    return os.getenv("MY_PEM_PATH")
