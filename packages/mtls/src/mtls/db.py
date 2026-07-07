"""DB/Postgres mTLS configuration for registry-state.

The enabled profile is intentionally fail-closed. The disabled profile is a
no-op so local SQLite/default development preserves pre-Epic-133 behavior.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import ssl
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, cast
from urllib.parse import parse_qs, urlsplit

from cryptography import x509
from cryptography.x509.oid import ExtensionOID

from mtls._exceptions import MTLSConfigError

_TRUTHY = frozenset({"true", "1", "yes", "on"})
_POSTGRES_ASYNCPG_SCHEME = "postgresql+asyncpg"
_POSTGRES_SCHEMES = frozenset(
    {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}
)
_DEFAULT_APPROVED_PREFIXES = ("/run/secrets/", "/certs/db/")
_ENV_PREFIX = "REGISTRY_DB_MTLS_"
_ALLOWED_SSLMODE = "verify-full"
_DEFAULT_ROTATION_WARNING_HOURS = 24
_DEFAULT_RETRY_MAX_ATTEMPTS = 1
_DEFAULT_RETRY_BACKOFF_SECONDS = 0
_SERIAL_RE = re.compile(r"^(?:0x)?[0-9a-fA-F][0-9a-fA-F:]*$|^[0-9]+$")


@dataclass(frozen=True)
class DBMTLSDiagnostic:
    """Sanitized DB mTLS diagnostic suitable for audit/failure records."""

    event: str
    failure_class: str
    config_family: str = _ENV_PREFIX
    max_attempts: int = _DEFAULT_RETRY_MAX_ATTEMPTS
    backoff_seconds: int = _DEFAULT_RETRY_BACKOFF_SECONDS
    fail_closed: bool = True

    def __post_init__(self) -> None:
        """Normalize public records to documented contract failure classes."""
        object.__setattr__(self, "failure_class", _public_failure_class(self.failure_class))

    def to_record(self) -> dict[str, Any]:
        """Return a path/hostname/DSN-free diagnostic record."""
        return {
            "event": self.event,
            "failure_class": self.failure_class,
            "config_family": self.config_family,
            "max_attempts": self.max_attempts,
            "backoff_seconds": self.backoff_seconds,
            "retry": {
                "max_attempts": self.max_attempts,
                "backoff_seconds": self.backoff_seconds,
                "plaintext_fallback": False,
            },
            "fail_closed": self.fail_closed,
        }


class DBMTLSConfigError(MTLSConfigError):
    """Sanitized fail-closed exception for DB mTLS configuration errors."""

    def __init__(self, failure_class: str, *, event: str = "db_mtls.setup_failed") -> None:
        public_class = _public_failure_class(failure_class)
        self.diagnostic = DBMTLSDiagnostic(event=event, failure_class=public_class)
        super().__init__(
            "DB mTLS configuration failed closed "
            f"(failure_class={public_class}, config_family={_ENV_PREFIX})"
        )


def _fail(failure_class: str) -> NoReturn:
    raise DBMTLSConfigError(failure_class)


@dataclass(frozen=True)
class DBMTLSSettings:
    """DB mTLS settings sourced from ``REGISTRY_DB_MTLS_*`` variables."""

    enabled: bool = False
    sslmode: str | None = None
    root_ca_path: str | None = None
    client_cert_path: str | None = None
    client_key_path: str | None = None
    server_hostname: str | None = None
    revocation_list_path: str | None = None
    server_cert_path: str | None = None
    server_key_path: str | None = None
    rotation_warning_hours: int = _DEFAULT_ROTATION_WARNING_HOURS
    approved_prefixes: tuple[str, ...] = _DEFAULT_APPROVED_PREFIXES
    ca_path: str | None = None
    cert_path: str | None = None
    key_path: str | None = None
    hostname: str | None = None
    crl_path: str | None = None
    server_cert_evidence_path: str | None = None
    server_certificate_path: str | None = None
    approved_secret_prefixes: tuple[str, ...] | list[str] | None = None
    test_approved_secret_prefixes: tuple[str, ...] | list[str] | None = None
    allow_test_secret_prefixes: tuple[str, ...] | list[str] | None = None

    def __post_init__(self) -> None:
        """Normalize backward/test aliases, then validate enabled settings."""
        alias_pairs = (
            ("root_ca_path", self.ca_path),
            ("client_cert_path", self.cert_path),
            ("client_key_path", self.key_path),
            ("server_hostname", self.hostname),
            ("revocation_list_path", self.crl_path),
            ("server_cert_path", self.server_cert_evidence_path or self.server_certificate_path),
        )
        for canonical, alias_value in alias_pairs:
            if getattr(self, canonical) is None and alias_value is not None:
                object.__setattr__(self, canonical, alias_value)
        if self.sslmode is None:
            object.__setattr__(self, "sslmode", _ALLOWED_SSLMODE)
        prefixes = (
            self.approved_secret_prefixes
            or self.test_approved_secret_prefixes
            or self.allow_test_secret_prefixes
        )
        if prefixes:
            object.__setattr__(self, "approved_prefixes", tuple(str(item) for item in prefixes))
        if self.enabled:
            self.validate_required()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DBMTLSSettings:
        """Build settings from environment, preserving disabled no-op behavior."""
        source = os.environ if env is None else env
        raw_hours = source.get("REGISTRY_DB_MTLS_ROTATION_WARNING_HOURS", "").strip()
        raw_prefixes = source.get("REGISTRY_DB_MTLS_APPROVED_PREFIXES", "").strip()
        if not raw_prefixes:
            raw_prefixes = source.get("REGISTRY_DB_MTLS_APPROVED_SECRET_PREFIXES", "").strip()
        if not raw_prefixes:
            raw_prefixes = source.get("REGISTRY_DB_MTLS_TEST_APPROVED_SECRET_PREFIXES", "").strip()
        prefixes: tuple[str, ...] = _DEFAULT_APPROVED_PREFIXES
        if raw_prefixes:
            prefixes = tuple(
                part.strip() for part in raw_prefixes.split(os.pathsep) if part.strip()
            )
        try:
            warning_hours = int(raw_hours) if raw_hours else _DEFAULT_ROTATION_WARNING_HOURS
        except ValueError:
            warning_hours = _DEFAULT_ROTATION_WARNING_HOURS
        sslmode = _env_first(source, "REGISTRY_DB_MTLS_SSLMODE")
        settings = cls(
            enabled=db_mtls_enabled(source),
            sslmode=sslmode.lower() if sslmode else None,
            root_ca_path=_env_first(
                source,
                "REGISTRY_DB_MTLS_ROOT_CA_PATH",
                "REGISTRY_DB_MTLS_ROOT_CA",
                "REGISTRY_DB_MTLS_CA_PATH",
                "REGISTRY_DB_MTLS_ROOT_CERT_PATH",
            ),
            client_cert_path=_env_first(
                source,
                "REGISTRY_DB_MTLS_CLIENT_CERT_PATH",
                "REGISTRY_DB_MTLS_CLIENT_CERT",
            ),
            client_key_path=_env_first(
                source,
                "REGISTRY_DB_MTLS_CLIENT_KEY_PATH",
                "REGISTRY_DB_MTLS_CLIENT_KEY",
            ),
            server_hostname=_env_first(
                source,
                "REGISTRY_DB_MTLS_SERVER_HOSTNAME",
                "REGISTRY_DB_MTLS_SERVER_HOST",
            ),
            revocation_list_path=_env_first(
                source,
                "REGISTRY_DB_MTLS_REVOCATION_LIST_PATH",
                "REGISTRY_DB_MTLS_REVOCATION_LIST",
                "REGISTRY_DB_MTLS_CRL_PATH",
            ),
            server_cert_path=_env_first(
                source,
                "REGISTRY_DB_MTLS_SERVER_CERT_PATH",
                "REGISTRY_DB_MTLS_SERVER_CERT_EVIDENCE_PATH",
                "REGISTRY_DB_MTLS_SERVER_CERTIFICATE_PATH",
            ),
            server_key_path=_env_first(source, "REGISTRY_DB_MTLS_SERVER_KEY_PATH"),
            rotation_warning_hours=warning_hours,
            approved_prefixes=prefixes,
        )
        return settings

    def validate_required(self) -> None:
        """Fail closed if required enabled-profile settings are absent or unsafe."""
        if self.sslmode != _ALLOWED_SSLMODE:
            _fail("plaintext_attempt")
        if not self.root_ca_path:
            _fail("invalid_ca")
        if not self.client_cert_path:
            _fail("missing_client_cert")
        if not self.client_key_path:
            _fail("missing_client_cert")
        if not self.server_hostname:
            _fail("hostname_mismatch")
        if not self.revocation_list_path:
            _fail("unreadable_material")
        if not self.approved_prefixes:
            _fail("unapproved_secret_path")


def _env_optional(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _env_first(env: Mapping[str, str], *keys: str) -> str | None:
    for key in keys:
        if value := _env_optional(env, key):
            return value
    return None


def db_mtls_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return ``True`` when DB mTLS is explicitly enabled."""
    source = os.environ if env is None else env
    return source.get("REGISTRY_DB_MTLS_ENABLED", "").strip().lower() in _TRUTHY


def db_mtls_failure_record(exc: BaseException, **_ignored: Any) -> dict[str, Any]:
    """Return a sanitized failure record for DB mTLS setup exceptions."""
    diagnostic = getattr(exc, "diagnostic", None)
    if isinstance(diagnostic, DBMTLSDiagnostic):
        return diagnostic.to_record()
    return DBMTLSDiagnostic(
        event="db_mtls.setup_failed",
        failure_class="unreadable_material",
    ).to_record()


def build_db_mtls_connect_args(
    database_url: str,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, ssl.SSLContext]:
    """Return SQLAlchemy/asyncpg ``connect_args`` for DB mTLS."""
    settings = DBMTLSSettings.from_env(env)
    if not settings.enabled:
        return {}
    assert settings.server_hostname is not None
    validate_db_mtls_database_url(database_url)
    url_host = urlsplit(database_url).hostname
    if not url_host or url_host.lower() != settings.server_hostname.lower():
        _fail("hostname_mismatch")
    return {"ssl": create_db_mtls_ssl_context(settings=settings)}


create_db_mtls_connect_args = build_db_mtls_connect_args


def create_asyncpg_connect_args(
    database_url: str,
    settings: DBMTLSSettings | None = None,
) -> dict[str, ssl.SSLContext]:
    """Compatibility wrapper used by tests/callers that pass explicit settings."""
    if settings is None:
        return build_db_mtls_connect_args(database_url)
    if not settings.enabled:
        return {}
    settings.validate_required()
    validate_db_mtls_database_url(database_url)
    url_host = urlsplit(database_url).hostname
    if (
        not url_host
        or not settings.server_hostname
        or url_host.lower() != settings.server_hostname.lower()
    ):
        _fail("hostname_mismatch")
    return {"ssl": create_db_mtls_ssl_context(settings=settings)}


build_asyncpg_connect_args = create_asyncpg_connect_args


def validate_db_mtls_database_url(database_url: str) -> None:
    """Fail closed on non-Postgres, non-asyncpg, or unsafe SSL mode URLs."""
    split = urlsplit(database_url)
    scheme = split.scheme
    if scheme != _POSTGRES_ASYNCPG_SCHEME:
        _fail("plaintext_attempt")
    query = parse_qs(split.query, keep_blank_values=True)
    sslmodes = [value.strip().lower() for value in query.get("sslmode", [])]
    if any(value != _ALLOWED_SSLMODE for value in sslmodes):
        _fail("plaintext_attempt")


def create_db_mtls_ssl_context(
    settings: DBMTLSSettings | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> ssl.SSLContext:
    """Build a hostname-checking asyncpg SSL context for DB mTLS."""
    settings = DBMTLSSettings.from_env(env) if settings is None else settings
    if not settings.enabled:
        _fail("plaintext_attempt")
    settings.validate_required()

    assert settings.root_ca_path is not None
    assert settings.client_cert_path is not None
    assert settings.client_key_path is not None
    assert settings.server_hostname is not None
    assert settings.revocation_list_path is not None

    root_ca = _validate_material_path(
        settings.root_ca_path,
        settings.approved_prefixes,
        failure_class="invalid_ca",
        expect_certificate=True,
    )
    _check_certificate_fresh(
        _load_certificate(root_ca, failure_class="invalid_ca"),
        settings.rotation_warning_hours,
    )
    client_cert = _validate_material_path(
        settings.client_cert_path,
        settings.approved_prefixes,
        failure_class="missing_client_cert",
        expect_certificate=True,
    )
    client_key = _validate_material_path(
        settings.client_key_path,
        settings.approved_prefixes,
        failure_class="missing_client_key",
        private_key=True,
    )

    client_x509 = _load_certificate(client_cert, failure_class="missing_client_cert")
    _check_certificate_fresh(client_x509, settings.rotation_warning_hours)

    revocation_path = _validate_material_path(
        settings.revocation_list_path,
        settings.approved_prefixes,
        failure_class="invalid_revocation_list",
    )
    revoked_serials = _load_revoked_serials(revocation_path, require_pem=True)
    _reject_if_revoked(client_x509, revoked_serials)

    if settings.server_cert_path:
        server_cert = _validate_material_path(
            settings.server_cert_path,
            settings.approved_prefixes,
            failure_class="invalid_server_cert",
            expect_certificate=True,
        )
        server_x509 = _load_certificate(server_cert, failure_class="invalid_server_cert")
        _check_certificate_fresh(server_x509, settings.rotation_warning_hours)
        _reject_if_revoked(server_x509, revoked_serials)
        if not certificate_matches_hostname(server_x509, settings.server_hostname):
            _fail("hostname_mismatch")

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = True
    ctx.options |= ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
    try:
        ctx.load_verify_locations(cafile=str(root_ca))
        if revocation_path is not None:
            ctx.load_verify_locations(cafile=str(revocation_path))
            ctx.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF
        ctx.load_cert_chain(certfile=str(client_cert), keyfile=str(client_key))
    except (OSError, ssl.SSLError):
        raise DBMTLSConfigError("unreadable_material") from None
    return ctx


def _validate_material_path(
    raw_path: str,
    approved_prefixes: tuple[str, ...],
    *,
    failure_class: str,
    expect_certificate: bool = False,
    private_key: bool = False,
) -> Path:
    """Validate canonical approved-prefix, symlink, mode, and readability policy."""
    try:
        resolved = Path(raw_path).expanduser().resolve(strict=True)
    except OSError:
        raise DBMTLSConfigError("unreadable_material") from None
    if not _is_under_approved_prefix(resolved, approved_prefixes):
        _fail("unapproved_secret_path")
    try:
        st = resolved.stat()
    except OSError:
        raise DBMTLSConfigError("unreadable_material") from None
    if not stat.S_ISREG(st.st_mode):
        _fail("unreadable_material")
    if st.st_mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH) == 0:
        _fail("unreadable_material")
    if not os.access(resolved, os.R_OK):
        _fail("unreadable_material")
    if private_key and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        _fail("wrong_permissions")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        _fail("wrong_permissions")
    if expect_certificate:
        _load_certificate(resolved, failure_class=failure_class)
    return resolved


def _is_under_approved_prefix(path: Path, approved_prefixes: tuple[str, ...]) -> bool:
    for raw_prefix in approved_prefixes:
        try:
            prefix = Path(raw_prefix).expanduser().resolve(strict=True)
        except OSError:
            continue
        try:
            path.relative_to(prefix)
        except ValueError:
            continue
        return True
    return False


def _load_certificate(path: Path, *, failure_class: str) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(path.read_bytes())
    except (OSError, ValueError):
        raise DBMTLSConfigError(failure_class) from None


def _check_certificate_fresh(cert: x509.Certificate, warning_hours: int) -> None:
    now = datetime.now(UTC)
    not_after = cert.not_valid_after_utc
    if now >= not_after:
        _fail("expired_cert")
    remaining = not_after - now
    if remaining <= timedelta(hours=warning_hours):
        logging.getLogger(__name__).warning(
            "DB mTLS certificate rotation window reached",
            extra={
                "event": "db_mtls.rotation_warning",
                "failure_class": "rotation_warning",
                "config_family": _ENV_PREFIX,
            },
        )


def _load_revoked_serials(path: Path, *, require_pem: bool = False) -> set[int]:
    try:
        raw = path.read_bytes()
    except OSError:
        raise DBMTLSConfigError("unreadable_material") from None
    try:
        crl = x509.load_pem_x509_crl(raw)
    except ValueError:
        if require_pem:
            _fail("invalid_revocation_list")
        return _load_serial_text(raw)
    return {cert.serial_number for cert in crl}


def _load_serial_text(raw: bytes) -> set[int]:
    try:
        text = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        raise DBMTLSConfigError("invalid_revocation_list") from None
    serials: set[int] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        value = line.split("#", 1)[0].strip()
        if not _SERIAL_RE.match(value):
            _fail("malformed_revocation_list")
        normalized = value.lower().removeprefix("0x").replace(":", "")
        base = 10 if value.isdecimal() else 16
        serials.add(int(normalized, base))
    return serials


def _reject_if_revoked(cert: x509.Certificate, revoked_serials: set[int]) -> None:
    if cert.serial_number in revoked_serials:
        _fail("revoked_cert")


def certificate_san_values(cert: x509.Certificate) -> tuple[str, ...]:
    """Return SAN values for validation helpers; callers must not log them."""
    try:
        san = cast(
            x509.SubjectAlternativeName,
            cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value,
        )
    except x509.ExtensionNotFound:
        return ()
    values: list[str] = []
    values.extend(san.get_values_for_type(x509.DNSName))
    values.extend(str(ip) for ip in san.get_values_for_type(x509.IPAddress))
    return tuple(values)


def certificate_matches_hostname(cert: x509.Certificate, hostname: str) -> bool:
    """Return whether a certificate SAN matches *hostname* for verify-full."""
    try:
        san = cast(
            x509.SubjectAlternativeName,
            cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value,
        )
    except x509.ExtensionNotFound:
        return False
    try:
        wanted_ip = ipaddress.ip_address(hostname)
    except ValueError:
        wanted_ip = None
    if wanted_ip is not None:
        return wanted_ip in san.get_values_for_type(x509.IPAddress)
    for pattern in san.get_values_for_type(x509.DNSName):
        if _dnsname_match(pattern, hostname):
            return True
    return False


def _dnsname_match(pattern: str, hostname: str) -> bool:
    pattern = pattern.rstrip(".").lower()
    hostname = hostname.rstrip(".").lower()
    if "*" not in pattern:
        return pattern == hostname
    left, _, suffix = pattern.partition(".")
    if left != "*" or not suffix:
        return False
    host_left, _, host_suffix = hostname.partition(".")
    return bool(host_left) and host_suffix == suffix


def validate_certificate_hostname(cert_path: str | Path, hostname: str) -> None:
    """Fail closed when *cert_path* does not match *hostname* by SAN."""
    cert = _load_certificate(Path(cert_path), failure_class="invalid_server_cert")
    if not certificate_matches_hostname(cert, hostname):
        _fail("hostname_mismatch")


def validate_static_server_contract(contract: Mapping[str, Any]) -> DBMTLSDiagnostic:
    """Validate static Postgres server-side mTLS evidence."""
    prefixes = _approved_prefixes_from_contract(contract)
    postgresql_conf = _mapping_value(contract.get("postgresql_conf")) or contract
    if str(postgresql_conf.get("ssl", "")).lower() not in {"on", "true", "1"}:
        _fail("server_contract_invalid")
    for key in ("ssl_cert_file", "ssl_key_file", "ssl_ca_file"):
        value = postgresql_conf.get(key)
        if not isinstance(value, str) or not value.strip():
            _fail("server_contract_invalid")
        assert isinstance(value, str)
        _validate_contract_path(value, prefixes, private_key=key == "ssl_key_file")
    revocation_claimed = bool(contract.get("revocation_claimed") or contract.get("revocation"))
    has_crl = bool(postgresql_conf.get("ssl_crl_file") or postgresql_conf.get("ssl_crl_dir"))
    if revocation_claimed and not has_crl:
        _fail("server_contract_invalid")
    for key in ("ssl_crl_file", "ssl_crl_dir"):
        value = postgresql_conf.get(key)
        if isinstance(value, str) and value.strip():
            _validate_contract_path(value, prefixes, directory=key == "ssl_crl_dir")
    _validate_pg_hba_contract(contract)
    return DBMTLSDiagnostic(
        event="db_mtls.server_contract_valid",
        failure_class="ok",
        fail_closed=False,
    )


def validate_postgres_server_contract(
    evidence: Mapping[str, Any],
    settings: DBMTLSSettings | None = None,
) -> DBMTLSDiagnostic:
    """Validate static Postgres mTLS evidence, including old-client revocation."""
    if (
        settings is not None
        and evidence.get("client_certificate_path")
        and evidence.get("ssl_crl_file")
    ):
        client_path = _validate_material_path(
            str(evidence["client_certificate_path"]),
            settings.approved_prefixes,
            failure_class="server_contract_invalid",
            expect_certificate=True,
        )
        crl_path = _validate_material_path(
            str(evidence["ssl_crl_file"]),
            settings.approved_prefixes,
            failure_class="server_contract_invalid",
        )
        client_cert = _load_certificate(client_path, failure_class="server_contract_invalid")
        _reject_if_revoked(client_cert, _load_revoked_serials(crl_path))
    contract = dict(evidence)
    contract.setdefault("sslmode_disable_rejected", True)
    return validate_static_server_contract(contract)


def _mapping_value(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _approved_prefixes_from_contract(contract: Mapping[str, Any]) -> tuple[str, ...]:
    value = contract.get("approved_prefixes") or contract.get("approved_secret_prefixes")
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(os.pathsep) if part.strip())
    if isinstance(value, (list, tuple)):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return _DEFAULT_APPROVED_PREFIXES


def _validate_contract_path(
    raw_path: str,
    prefixes: tuple[str, ...],
    *,
    private_key: bool = False,
    directory: bool = False,
) -> None:
    if directory:
        try:
            resolved = Path(raw_path).expanduser().resolve(strict=True)
        except OSError:
            raise DBMTLSConfigError("server_contract_invalid") from None
        if not resolved.is_dir() or not _is_under_approved_prefix(resolved, prefixes):
            _fail("server_contract_invalid")
        return
    try:
        _validate_material_path(
            raw_path,
            prefixes,
            failure_class="server_contract_invalid",
            private_key=private_key,
        )
    except DBMTLSConfigError:
        raise DBMTLSConfigError("server_contract_invalid") from None


def _validate_pg_hba_contract(contract: Mapping[str, Any]) -> None:
    raw_hba = contract.get("pg_hba") or contract.get("pg_hba_conf") or contract.get("pg_hba_rules")
    application_database = _contract_string(
        contract,
        "application_database",
        "database",
        "dbname",
        "target_database",
    )
    application_role = _contract_string(
        contract,
        "application_role",
        "role",
        "user",
        "target_role",
    )
    if not application_database or not application_role:
        _fail("server_contract_invalid")
    if isinstance(raw_hba, str):
        lines = raw_hba.splitlines()
    elif isinstance(raw_hba, (list, tuple)):
        lines = [str(item) for item in raw_hba]
    else:
        _fail("server_contract_invalid")
    saw_hostssl = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        record_type = fields[0].lower() if fields else ""
        if record_type not in {"host", "hostssl"}:
            continue
        if len(fields) < 5:
            _fail("server_contract_invalid")
        rule_database = fields[1]
        rule_role = fields[2]
        method = fields[4].lower()
        matches_target = _pg_hba_token_matches(rule_database, application_database) and (
            _pg_hba_token_matches(rule_role, application_role)
        )
        if record_type == "host" and matches_target and method != "reject":
            _fail("server_contract_invalid")
        has_clientcert_verify = any(
            field.lower() in {"clientcert", "clientcert=verify-full", "clientcert=verify-ca"}
            for field in fields[5:]
        )
        if (
            record_type == "hostssl"
            and matches_target
            and rule_database == application_database
            and rule_role == application_role
            and method == "cert"
            and has_clientcert_verify
        ):
            saw_hostssl = True
    if not saw_hostssl:
        _fail("server_contract_invalid")
    if contract.get("sslmode_disable_rejected") is not True:
        _fail("server_contract_invalid")


def _contract_string(contract: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = contract.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _pg_hba_token_matches(rule_token: str, target: str) -> bool:
    entries = [part.strip() for part in rule_token.split(",")]
    return any(entry in {"all", target} for entry in entries)


__all__ = [
    "DBMTLSConfigError",
    "DBMTLSDiagnostic",
    "DBMTLSSettings",
    "build_asyncpg_connect_args",
    "build_asyncpg_ssl_context",
    "build_db_mtls_connect_args",
    "certificate_matches_hostname",
    "certificate_san_values",
    "create_db_mtls_connect_args",
    "create_asyncpg_connect_args",
    "create_db_mtls_ssl_context",
    "db_mtls_enabled",
    "db_mtls_failure_record",
    "validate_certificate_hostname",
    "validate_db_mtls_database_url",
    "validate_postgres_server_contract",
    "validate_static_server_contract",
]


def _public_failure_class(failure_class: str) -> str:
    """Map internal compatibility names to the documented DB mTLS contract classes."""
    return {
        "expired_certificate": "expired_cert",
        "revoked_certificate": "revoked_cert",
        "unsafe_sslmode": "plaintext_attempt",
        "unsupported_database_url": "plaintext_attempt",
        "unsupported_postgres_driver": "plaintext_attempt",
        "disabled": "plaintext_attempt",
        "missing_required_material": "missing_client_cert",
        "missing_client_key": "missing_client_cert",
        "invalid_revocation_list": "unreadable_material",
        "malformed_revocation_list": "unreadable_material",
        "invalid_server_cert": "invalid_ca",
        "unapproved_secret_path": "unreadable_material",
        "symlink_escape": "unreadable_material",
        "server_contract_invalid": "plaintext_attempt",
        "db_mtls_setup_failed": "unreadable_material",
    }.get(failure_class, failure_class)


build_asyncpg_ssl_context = create_db_mtls_ssl_context
