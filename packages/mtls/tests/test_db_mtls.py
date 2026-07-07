"""Epic 133 targeted DB mTLS tests for the mtls package."""

from __future__ import annotations

import datetime as dt
import importlib
import inspect
import json
import ssl
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

FAILURE_CLASSES = {
    "invalid_ca",
    "expired_cert",
    "revoked_cert",
    "hostname_mismatch",
    "missing_client_cert",
    "wrong_permissions",
    "unreadable_material",
    "plaintext_attempt",
}

FORBIDDEN_FAILURE_CLASS_ALIASES = {
    "expired_certificate",
    "revoked_certificate",
    "unsafe_sslmode",
    "unsupported_database_url",
    "missing_required_material",
    "db_mtls_setup_failed",
}


@dataclass(frozen=True)
class Material:
    base: Path
    outside: Path
    ca_cert: Path
    client_cert: Path
    client_key: Path
    server_cert: Path
    revocation_list: Path
    old_server_cert: Path
    old_client_cert: Path
    ca: x509.Certificate
    client: x509.Certificate
    server: x509.Certificate
    old_server: x509.Certificate
    old_client: x509.Certificate
    ca_key: rsa.RSAPrivateKey
    host: str = "db-mtls-target.test"

    @property
    def url(self) -> str:
        return "postgresql+asyncpg://app:secret-password@db-mtls-target.test:5432/registry"

    @property
    def forbidden(self) -> tuple[str, ...]:
        return (
            "BEGIN CERTIFICATE",
            "BEGIN PRIVATE KEY",
            "secret-password",
            self.url,
            self.host,
            "sensitive-client-cn",
            "sensitive-server-cn",
            str(self.base),
            str(self.outside),
            self.ca_cert.name,
            self.client_cert.name,
            self.client_key.name,
            self.server_cert.name,
            self.revocation_list.name,
        )


def _api() -> Any:
    return importlib.import_module("mtls.db")


def _err(api: Any) -> type[Exception]:
    return cast(
        "type[Exception]",
        getattr(api, "MTLSConfigError", importlib.import_module("mtls").MTLSConfigError),
    )


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _ca(key: rsa.RSAPrivateKey) -> x509.Certificate:
    now = dt.datetime.now(dt.UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(_name("epic-133-ca"))
        .issuer_name(_name("epic-133-ca"))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )


def _leaf(
    key: rsa.RSAPrivateKey,
    ca_key: rsa.RSAPrivateKey,
    ca: x509.Certificate,
    *,
    cn: str,
    dns: str,
    usage: Any,
    not_after: dt.datetime | None = None,
) -> x509.Certificate:
    now = dt.datetime.now(dt.UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(hours=1))
        .not_valid_after(not_after or (now + dt.timedelta(days=7)))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(dns)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )


def _put_bytes(path: Path, data: bytes, mode: int = 0o644) -> Path:
    with path.open("wb") as fh:
        fh.write(data)
    path.chmod(mode)
    return path


def _put_cert(path: Path, cert: x509.Certificate) -> Path:
    return _put_bytes(path, cert.public_bytes(serialization.Encoding.PEM))


def _put_key(path: Path, key: rsa.RSAPrivateKey) -> Path:
    data = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    out = _put_bytes(path, data, 0o600)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    return out


def _put_crl(
    path: Path,
    *,
    ca: x509.Certificate,
    ca_key: rsa.RSAPrivateKey,
    revoked: list[x509.Certificate],
) -> Path:
    now = dt.datetime.now(dt.UTC)
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca.subject)
        .last_update(now - dt.timedelta(minutes=1))
        .next_update(now + dt.timedelta(days=1))
    )
    for cert in revoked:
        builder = builder.add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(cert.serial_number)
            .revocation_date(now)
            .build()
        )
    crl = builder.sign(private_key=ca_key, algorithm=hashes.SHA256())
    return _put_bytes(path, crl.public_bytes(serialization.Encoding.PEM))


@pytest.fixture()
def material(tmp_path: Path) -> Material:
    base = tmp_path / "approved"
    outside = tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    ca_key = _key()
    ca = _ca(ca_key)
    client_key = _key()
    server_key = _key()
    old_server_key = _key()
    old_client_key = _key()
    client = _leaf(
        client_key,
        ca_key,
        ca,
        cn="sensitive-client-cn",
        dns="client-mtls.test",
        usage=ExtendedKeyUsageOID.CLIENT_AUTH,
    )
    server = _leaf(
        server_key,
        ca_key,
        ca,
        cn="sensitive-server-cn",
        dns="db-mtls-target.test",
        usage=ExtendedKeyUsageOID.SERVER_AUTH,
    )
    old_server = _leaf(
        old_server_key,
        ca_key,
        ca,
        cn="old-sensitive-server-cn",
        dns="old-db-mtls-target.test",
        usage=ExtendedKeyUsageOID.SERVER_AUTH,
    )
    old_client = _leaf(
        old_client_key,
        ca_key,
        ca,
        cn="old-sensitive-client-cn",
        dns="old-client-mtls.test",
        usage=ExtendedKeyUsageOID.CLIENT_AUTH,
    )
    return Material(
        base=base,
        outside=outside,
        ca_cert=_put_cert(base / "sensitive-ca.pem", ca),
        client_cert=_put_cert(base / "sensitive-client.pem", client),
        client_key=_put_key(base / "sensitive-key.pem", client_key),
        server_cert=_put_cert(base / "sensitive-server.pem", server),
        revocation_list=_put_crl(
            base / "sensitive-empty-crl.pem",
            ca=ca,
            ca_key=ca_key,
            revoked=[],
        ),
        old_server_cert=_put_cert(base / "old-sensitive-server.pem", old_server),
        old_client_cert=_put_cert(base / "old-sensitive-client.pem", old_client),
        ca=ca,
        client=client,
        server=server,
        old_server=old_server,
        old_client=old_client,
        ca_key=ca_key,
    )


def _accepted(obj: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    fields = getattr(obj, "model_fields", None)
    if isinstance(fields, dict):
        return {key: value for key, value in kwargs.items() if key in fields}
    sig = inspect.signature(obj)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return kwargs
    return {key: value for key, value in kwargs.items() if key in sig.parameters}


def _settings(api: Any, m: Material, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "enabled": True,
        "root_ca_path": str(m.ca_cert),
        "ca_path": str(m.ca_cert),
        "client_cert_path": str(m.client_cert),
        "cert_path": str(m.client_cert),
        "client_key_path": str(m.client_key),
        "key_path": str(m.client_key),
        "server_hostname": m.host,
        "hostname": m.host,
        "revocation_list_path": str(m.revocation_list),
        "crl_path": str(m.revocation_list),
        "server_cert_evidence_path": str(m.server_cert),
        "server_certificate_path": str(m.server_cert),
        "approved_secret_prefixes": [str(m.base)],
        "test_approved_secret_prefixes": [str(m.base)],
        "allow_test_secret_prefixes": [str(m.base)],
        "rotation_warning_hours": 24,
    }
    kwargs.update(overrides)
    return api.DBMTLSSettings(**_accepted(api.DBMTLSSettings, kwargs))


def _disabled(api: Any) -> Any:
    return api.DBMTLSSettings(**_accepted(api.DBMTLSSettings, {"enabled": False}))


def _connect_args(api: Any, url: str, settings: Any) -> dict[str, Any]:
    func = api.create_asyncpg_connect_args
    kwargs = _accepted(func, {"url": url, "db_url": url, "database_url": url, "settings": settings})
    if kwargs:
        return cast("dict[str, Any]", func(**kwargs))
    params = len(inspect.signature(func).parameters)
    if params == 0:
        return cast("dict[str, Any]", func())
    if params == 1:
        try:
            return cast("dict[str, Any]", func(settings))
        except TypeError:
            return cast("dict[str, Any]", func(url))
    return cast("dict[str, Any]", func(url, settings))


def _context(api: Any, settings: Any) -> ssl.SSLContext:
    func = api.build_asyncpg_ssl_context
    kwargs = _accepted(func, {"settings": settings})
    return cast("ssl.SSLContext", func(**kwargs) if kwargs else func(settings))


def _record(api: Any, exc: BaseException, url: str) -> dict[str, Any]:
    func = api.db_mtls_failure_record
    kwargs = _accepted(
        func, {"exc": exc, "exception": exc, "error": exc, "url": url, "db_url": url}
    )
    if kwargs:
        return cast("dict[str, Any]", func(**kwargs))
    try:
        return cast("dict[str, Any]", func(exc))
    except TypeError:
        return cast("dict[str, Any]", func("db_mtls", exc))


def _safe(value: Any, m: Material, *extra: str) -> None:
    rendered = (
        json.dumps(value, default=str, sort_keys=True) if not isinstance(value, str) else value
    )
    for token in (*m.forbidden, *extra):
        assert token not in rendered


def _has_failure_class(value: Any) -> None:
    rendered = json.dumps(value, default=str)
    assert any(name in rendered for name in FAILURE_CLASSES), rendered
    assert not any(name in rendered for name in FORBIDDEN_FAILURE_CLASS_ALIASES), rendered


def test_disabled_settings_return_no_db_ssl_connect_args(material: Material) -> None:
    api = _api()
    assert "ssl" not in _connect_args(api, material.url, _disabled(api))


def test_diagnostic_records_normalize_legacy_failure_class_aliases() -> None:
    api = _api()
    record = api.DBMTLSDiagnostic(
        event="db_mtls.setup_failed", failure_class="expired_certificate"
    ).to_record()
    assert record["failure_class"] == "expired_cert"
    rendered = json.dumps(record, sort_keys=True)
    assert "expired_certificate" not in rendered


@pytest.mark.parametrize(
    ("missing", "expected_failure_class"),
    [
        ("root_ca_path", "invalid_ca"),
        ("client_cert_path", "missing_client_cert"),
        ("client_key_path", "missing_client_cert"),
        ("server_hostname", "hostname_mismatch"),
        ("revocation_list_path", "unreadable_material"),
    ],
)
def test_enabled_settings_require_all_material_and_hostname(
    material: Material, missing: str, expected_failure_class: str
) -> None:
    api = _api()
    aliases = {
        "root_ca_path": {"root_ca_path": None, "ca_path": None},
        "client_cert_path": {"client_cert_path": None, "cert_path": None},
        "client_key_path": {"client_key_path": None, "key_path": None},
        "server_hostname": {"server_hostname": None, "hostname": None},
        "revocation_list_path": {"revocation_list_path": None, "crl_path": None},
    }
    with pytest.raises(_err(api)) as excinfo:
        _settings(api, material, **aliases[missing])
    rendered = str(excinfo.value)
    assert expected_failure_class in rendered
    _has_failure_class(rendered)
    _safe(rendered, material)


@pytest.mark.parametrize("field", ["root_ca_path", "client_cert_path", "client_key_path"])
def test_unapproved_secret_paths_fail_closed_and_are_redacted(
    material: Material, field: str
) -> None:
    api = _api()
    outside = _put_bytes(material.outside / "outside-sensitive.pem", material.ca_cert.read_bytes())
    aliases = {
        "root_ca_path": {"root_ca_path": str(outside), "ca_path": str(outside)},
        "client_cert_path": {"client_cert_path": str(outside), "cert_path": str(outside)},
        "client_key_path": {"client_key_path": str(outside), "key_path": str(outside)},
    }
    with pytest.raises(_err(api)) as excinfo:
        _connect_args(api, material.url, _settings(api, material, **aliases[field]))
    _has_failure_class(str(excinfo.value))
    _safe(str(excinfo.value), material, outside.name)


def test_symlink_escape_under_approved_prefix_fails_closed(material: Material) -> None:
    api = _api()
    escaped = _put_bytes(
        material.outside / "escaped-key.pem", material.client_key.read_bytes(), 0o600
    )
    link = material.base / "looks-approved-key.pem"
    link.symlink_to(escaped)
    with pytest.raises(_err(api)) as excinfo:
        settings = _settings(api, material, client_key_path=str(link), key_path=str(link))
        _connect_args(api, material.url, settings)
    _has_failure_class(str(excinfo.value))
    _safe(str(excinfo.value), material, escaped.name, link.name)


@pytest.mark.parametrize("sslmode", ["disable", "allow", "prefer", "require", "verify-ca"])
def test_unsafe_sslmode_values_fail_closed(material: Material, sslmode: str) -> None:
    api = _api()
    with pytest.raises(_err(api)) as excinfo:
        _connect_args(
            api, f"{material.url}?sslmode={sslmode}", _settings(api, material, sslmode=sslmode)
        )
    rendered = str(excinfo.value)
    assert "plaintext_attempt" in rendered
    _has_failure_class(rendered)
    _safe(rendered, material)


@pytest.mark.parametrize(
    "url",
    [
        "sqlite+aiosqlite:///tmp/state.sqlite3",
        "mysql+asyncmy://app:secret@db.invalid/registry",
        "postgresql://app:secret@db.invalid/registry",
        "postgresql+psycopg://app:secret@db.invalid/registry",
    ],
)
def test_enabled_non_asyncpg_urls_fail_closed(material: Material, url: str) -> None:
    api = _api()
    with pytest.raises(_err(api)) as excinfo:
        _connect_args(api, url, _settings(api, material))
    rendered = str(excinfo.value)
    assert "plaintext_attempt" in rendered
    _has_failure_class(rendered)
    _safe(rendered, material, url)


def test_builds_verify_full_asyncpg_ssl_context_and_connect_args(material: Material) -> None:
    api = _api()
    settings = _settings(api, material)
    ctx = _context(api, settings)
    args = _connect_args(api, material.url, settings)
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert isinstance(args["ssl"], ssl.SSLContext)


def test_from_env_accepts_documented_root_ca_and_revocation_aliases(
    material: Material,
) -> None:
    api = _api()
    crl = _put_crl(
        material.base / "empty-sensitive-crl.pem",
        ca=material.ca,
        ca_key=material.ca_key,
        revoked=[],
    )
    settings = api.DBMTLSSettings.from_env(
        {
            "REGISTRY_DB_MTLS_ENABLED": "true",
            "REGISTRY_DB_MTLS_ROOT_CA": str(material.ca_cert),
            "REGISTRY_DB_MTLS_CLIENT_CERT": str(material.client_cert),
            "REGISTRY_DB_MTLS_CLIENT_KEY": str(material.client_key),
            "REGISTRY_DB_MTLS_SERVER_HOSTNAME": material.host,
            "REGISTRY_DB_MTLS_REVOCATION_LIST": str(crl),
            "REGISTRY_DB_MTLS_SERVER_CERT_EVIDENCE_PATH": str(material.server_cert),
            "REGISTRY_DB_MTLS_APPROVED_SECRET_PREFIXES": str(material.base),
        }
    )
    assert settings.root_ca_path == str(material.ca_cert)
    assert settings.revocation_list_path == str(crl)
    assert isinstance(_connect_args(api, material.url, settings)["ssl"], ssl.SSLContext)


def test_expiry_revocation_and_rotation_policy(material: Material) -> None:
    api = _api()
    expired_key = _key()
    expired = _leaf(
        expired_key,
        expired_key,
        material.ca,
        cn="expired-sensitive-client-cn",
        dns="client-mtls.test",
        usage=ExtendedKeyUsageOID.CLIENT_AUTH,
        not_after=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1),
    )
    expired_path = _put_cert(material.base / "expired-sensitive-client.pem", expired)
    with pytest.raises(_err(api)) as expired_exc:
        settings = _settings(
            api, material, client_cert_path=str(expired_path), cert_path=str(expired_path)
        )
        _connect_args(api, material.url, settings)
    expired_rendered = str(expired_exc.value)
    assert "expired_cert" in expired_rendered
    assert "expired_certificate" not in expired_rendered
    _has_failure_class(expired_rendered)
    _safe(expired_rendered, material, expired_path.name, "expired-sensitive-client-cn")

    crl = _put_crl(
        material.base / "revoked-sensitive-server-crl.pem",
        ca=material.ca,
        ca_key=material.ca_key,
        revoked=[material.old_server],
    )
    revoked = _settings(
        api,
        material,
        revocation_list_path=str(crl),
        crl_path=str(crl),
        server_cert_evidence_path=str(material.old_server_cert),
        server_certificate_path=str(material.old_server_cert),
    )
    with pytest.raises(_err(api)) as revoked_exc:
        _connect_args(api, material.url, revoked)
    revoked_rendered = str(revoked_exc.value)
    assert "revoked_cert" in revoked_rendered
    assert "revoked_certificate" not in revoked_rendered
    _has_failure_class(revoked_rendered)
    _safe(revoked_rendered, material, crl.name, material.old_server_cert.name)
    rotated_crl = _put_crl(
        material.base / "rotated-sensitive-server-crl.pem",
        ca=material.ca,
        ca_key=material.ca_key,
        revoked=[material.old_client],
    )
    rotated = _settings(
        api, material, revocation_list_path=str(rotated_crl), crl_path=str(rotated_crl)
    )
    ctx = _connect_args(api, material.url, rotated)["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_flags & ssl.VERIFY_CRL_CHECK_LEAF


def test_server_contract_rejects_revoked_old_client_evidence(material: Material) -> None:
    api = _api()
    crl = _put_bytes(
        material.base / "revoked-sensitive-clients.txt",
        f"{material.old_client.serial_number:x}\n".encode(),
    )
    evidence = {
        "ssl": "on",
        "ssl_cert_file": str(material.server_cert),
        "ssl_key_file": str(material.client_key),
        "ssl_ca_file": str(material.ca_cert),
        "ssl_crl_file": str(crl),
        "application_database": "registry",
        "application_role": "app",
        "pg_hba": ["hostssl registry app 0.0.0.0/0 cert clientcert=verify-full"],
        "client_certificate_path": str(material.old_client_cert),
    }
    func = api.validate_postgres_server_contract
    kwargs = _accepted(func, {"evidence": evidence, "settings": _settings(api, material)})
    with pytest.raises(_err(api)) as excinfo:
        func(**kwargs) if kwargs else func(evidence, _settings(api, material))
    _has_failure_class(str(excinfo.value))
    _safe(str(excinfo.value), material, crl.name, material.old_client_cert.name)


def test_san_mismatch_fails_hostname_validation(material: Material) -> None:
    api = _api()
    func = api.validate_certificate_hostname
    kwargs = _accepted(
        func, {"cert_path": str(material.server_cert), "hostname": "wrong-host.test"}
    )
    with pytest.raises(_err(api)) as excinfo:
        func(**kwargs) if kwargs else func(str(material.server_cert), "wrong-host.test")
    _has_failure_class(str(excinfo.value))
    _safe(str(excinfo.value), material, "wrong-host.test")


@pytest.mark.parametrize(
    ("field", "mode", "expected"),
    [
        ("root_ca_path", 0o000, "unreadable_material"),
        ("client_cert_path", 0o000, "unreadable_material"),
        ("client_key_path", 0o644, "wrong_permissions"),
        ("revocation_list_path", 0o000, "unreadable_material"),
    ],
)
def test_wrong_permissions_are_bounded_and_sanitized(
    material: Material,
    field: str,
    mode: int,
    expected: str,
) -> None:
    api = _api()
    target = material.base / f"bad-perms-{field}.pem"
    payload = (
        material.client_key.read_bytes()
        if field == "client_key_path"
        else material.ca_cert.read_bytes()
    )
    _put_bytes(target, payload, mode)
    alias = {
        "root_ca_path": "ca_path",
        "client_cert_path": "cert_path",
        "client_key_path": "key_path",
    }.get(
        field,
        "crl_path",
    )
    with pytest.raises(_err(api)) as excinfo:
        _connect_args(
            api, material.url, _settings(api, material, **{field: str(target), alias: str(target)})
        )
    rendered = str(excinfo.value)
    assert expected in rendered or any(name in rendered for name in FAILURE_CLASSES)
    _safe(rendered, material, target.name)


def test_malformed_revocation_and_logs_are_sanitized(
    material: Material,
    caplog: pytest.LogCaptureFixture,
) -> None:
    api = _api()
    crl = _put_bytes(
        material.base / "malformed-sensitive-crl.txt", b"not-a-serial\nBEGIN CERTIFICATE\n"
    )
    with pytest.raises(_err(api)) as excinfo:
        _connect_args(
            api,
            material.url,
            _settings(api, material, revocation_list_path=str(crl), crl_path=str(crl)),
        )
    _has_failure_class(str(excinfo.value))
    _safe(str(excinfo.value), material, crl.name, "not-a-serial")
    _safe(caplog.text, material, crl.name, "not-a-serial")


def test_live_mtls_revocation_requires_pem_crl_not_text_serials(
    material: Material,
) -> None:
    api = _api()
    crl = _put_bytes(
        material.base / "live-sensitive-serials.txt",
        f"{material.old_server.serial_number:x}\n".encode(),
    )
    with pytest.raises(_err(api)) as excinfo:
        _connect_args(
            api,
            material.url,
            _settings(api, material, revocation_list_path=str(crl), crl_path=str(crl)),
        )
    _has_failure_class(str(excinfo.value))
    _safe(str(excinfo.value), material, crl.name)


def test_pg_hba_contract_requires_exact_app_hostssl_and_only_matching_plaintext_bypasses(
    material: Material,
) -> None:
    api = _api()
    base_contract = {
        "ssl": "on",
        "ssl_cert_file": str(material.server_cert),
        "ssl_key_file": str(material.client_key),
        "ssl_ca_file": str(material.ca_cert),
        "approved_prefixes": [str(material.base)],
        "application_database": "registry",
        "application_role": "app",
        "sslmode_disable_rejected": True,
        "pg_hba": [
            "host unrelated other 0.0.0.0/0 md5",
            "hostssl registry app 0.0.0.0/0 cert clientcert=verify-full",
        ],
    }
    assert api.validate_static_server_contract(base_contract).failure_class == "ok"

    bad = dict(base_contract)
    bad["pg_hba"] = [
        "host registry app 0.0.0.0/0 md5",
        "hostssl registry app 0.0.0.0/0 cert clientcert=verify-full",
    ]
    with pytest.raises(_err(api)):
        api.validate_static_server_contract(bad)

    bypass_after_hostssl = dict(base_contract)
    bypass_after_hostssl["pg_hba"] = [
        "hostssl registry app 0.0.0.0/0 cert clientcert=verify-full",
        "host registry app 0.0.0.0/0 md5",
    ]
    with pytest.raises(_err(api)):
        api.validate_static_server_contract(bypass_after_hostssl)

    too_broad = dict(base_contract)
    too_broad["pg_hba"] = ["hostssl all all 0.0.0.0/0 cert clientcert=verify-full"]
    with pytest.raises(_err(api)):
        api.validate_static_server_contract(too_broad)


def test_failure_record_retry_audit_diagnostics_are_bounded_and_sanitized(
    material: Material,
) -> None:
    api = _api()
    try:
        _connect_args(
            api,
            material.url,
            _settings(
                api, material, client_key_path=str(material.outside / "missing-sensitive-key.pem")
            ),
        )
    except Exception as exc:  # noqa: BLE001
        record = _record(api, exc, material.url)
    else:
        pytest.fail("unapproved key path should fail closed")
    assert isinstance(record, dict)
    _has_failure_class(record)
    _safe(record, material, "missing-sensitive-key.pem")
    retry = record.get("retry") or record.get("retry_policy") or {}
    assert retry.get("plaintext_fallback") is not True
    if "max_attempts" in retry:
        assert 0 <= retry["max_attempts"] <= 3
