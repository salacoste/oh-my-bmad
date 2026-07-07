"""Epic 133 registry-state DB mTLS integration tests."""

from __future__ import annotations

import datetime as dt
import importlib
import inspect
import json
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DB_URL = "postgresql+asyncpg://app:secret-password@db-mtls-target.test:5432/registry"
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
class Fixture:
    base: Path
    ca_cert: Path
    client_cert: Path
    client_key: Path
    server_cert: Path
    revocation_list: Path

    @property
    def forbidden(self) -> tuple[str, ...]:
        return (
            "BEGIN CERTIFICATE",
            "BEGIN PRIVATE KEY",
            "secret-password",
            DB_URL,
            "db-mtls-target.test",
            "registry-sensitive-client-cn",
            "registry-sensitive-server-cn",
            str(self.base),
            self.ca_cert.name,
            self.client_cert.name,
            self.client_key.name,
            self.server_cert.name,
            self.revocation_list.name,
        )


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _ca(key: rsa.RSAPrivateKey) -> x509.Certificate:
    now = dt.datetime.now(dt.UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(_name("registry-epic-133-ca"))
        .issuer_name(_name("registry-epic-133-ca"))
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
) -> x509.Certificate:
    now = dt.datetime.now(dt.UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(hours=1))
        .not_valid_after(now + dt.timedelta(days=7))
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
def db_mtls_fixture(tmp_path: Path) -> Fixture:
    base = tmp_path / "registry-approved"
    base.mkdir()
    ca_key = _key()
    ca = _ca(ca_key)
    client_key = _key()
    server_key = _key()
    return Fixture(
        base=base,
        ca_cert=_put_cert(base / "registry-sensitive-ca.pem", ca),
        client_cert=_put_cert(
            base / "registry-sensitive-client.pem",
            _leaf(
                client_key,
                ca_key,
                ca,
                cn="registry-sensitive-client-cn",
                dns="registry-client.test",
                usage=ExtendedKeyUsageOID.CLIENT_AUTH,
            ),
        ),
        client_key=_put_key(base / "registry-sensitive-key.pem", client_key),
        server_cert=_put_cert(
            base / "registry-sensitive-server.pem",
            _leaf(
                server_key,
                ca_key,
                ca,
                cn="registry-sensitive-server-cn",
                dns="db-mtls-target.test",
                usage=ExtendedKeyUsageOID.SERVER_AUTH,
            ),
        ),
        revocation_list=_put_crl(
            base / "registry-sensitive-empty-crl.pem",
            ca=ca,
            ca_key=ca_key,
            revoked=[],
        ),
    )


@pytest.fixture()
def db_mtls_env(monkeypatch: pytest.MonkeyPatch, db_mtls_fixture: Fixture) -> None:
    values = {
        "REGISTRY_DB_MTLS_ENABLED": "true",
        "REGISTRY_DB_MTLS_ROOT_CA_PATH": str(db_mtls_fixture.ca_cert),
        "REGISTRY_DB_MTLS_CLIENT_CERT_PATH": str(db_mtls_fixture.client_cert),
        "REGISTRY_DB_MTLS_CLIENT_KEY_PATH": str(db_mtls_fixture.client_key),
        "REGISTRY_DB_MTLS_SERVER_HOSTNAME": "db-mtls-target.test",
        "REGISTRY_DB_MTLS_REVOCATION_LIST": str(db_mtls_fixture.revocation_list),
        "REGISTRY_DB_MTLS_SERVER_CERT_EVIDENCE_PATH": str(db_mtls_fixture.server_cert),
        "REGISTRY_DB_MTLS_APPROVED_SECRET_PREFIXES": str(db_mtls_fixture.base),
        "REGISTRY_DB_MTLS_TEST_APPROVED_SECRET_PREFIXES": str(db_mtls_fixture.base),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _cparams(engine: Any) -> dict[str, Any]:
    creator = engine.pool._creator_arg
    closure = creator.__closure__ or ()
    freevars = creator.__code__.co_freevars
    return cast(
        "dict[str, Any]",
        dict(zip(freevars, (cell.cell_contents for cell in closure), strict=True)).get(
            "cparams", {}
        ),
    )


def _safe(value: Any, fixture: Fixture, *extra: str) -> None:
    rendered = (
        json.dumps(value, default=str, sort_keys=True) if not isinstance(value, str) else value
    )
    for token in (*fixture.forbidden, *extra):
        assert token not in rendered


def _has_failure_class(value: Any) -> None:
    rendered = json.dumps(value, default=str)
    assert any(name in rendered for name in FAILURE_CLASSES), rendered
    assert not any(name in rendered for name in FORBIDDEN_FAILURE_CLASS_ALIASES), rendered


def test_postgres_engine_adds_asyncpg_ssl_only_when_db_mtls_enabled(
    monkeypatch: pytest.MonkeyPatch,
    db_mtls_env: None,
) -> None:
    from registry_state.adapters.sqlite_store import create_engine

    enabled = create_engine(DB_URL)
    assert "ssl" in _cparams(enabled)
    monkeypatch.setenv("REGISTRY_DB_MTLS_ENABLED", "false")
    disabled = create_engine(DB_URL)
    assert "ssl" not in _cparams(disabled)


def test_disabled_sqlite_and_postgres_defaults_are_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

    from registry_state.adapters.sqlite_store import create_engine

    monkeypatch.delenv("REGISTRY_DB_MTLS_ENABLED", raising=False)
    sqlite_engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'state.sqlite3'}")
    postgres_engine = create_engine(DB_URL)
    assert isinstance(sqlite_engine.pool, NullPool)
    assert "ssl" not in _cparams(sqlite_engine)
    assert isinstance(postgres_engine.pool, AsyncAdaptedQueuePool)
    assert "ssl" not in _cparams(postgres_engine)


def test_postgres_read_only_error_redacts_raw_dsn() -> None:
    from registry_state.adapters.sqlite_store import create_engine

    with pytest.raises(ValueError) as excinfo:
        create_engine(DB_URL, read_only=True)
    rendered = str(excinfo.value)
    assert "postgresql+asyncpg" in rendered
    assert DB_URL not in rendered
    assert "secret-password" not in rendered
    assert "db-mtls-target.test" not in rendered


def test_non_sqlite_read_only_error_redacts_raw_dsn_and_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from registry_state.adapters.sqlite_store import create_engine

    monkeypatch.delenv("REGISTRY_DB_MTLS_ENABLED", raising=False)
    url = "mysql+asyncmy://app:supersensitive-password@db.internal.example/registry"
    with pytest.raises(ValueError) as excinfo:
        create_engine(url, read_only=True)
    rendered = str(excinfo.value)
    assert "mysql+asyncmy" in rendered
    assert url not in rendered
    assert "supersensitive-password" not in rendered
    assert "db.internal.example" not in rendered


@pytest.mark.parametrize(
    "url",
    [
        "sqlite+aiosqlite:///tmp/state.sqlite3",
        "mysql+asyncmy://app:secret@db.invalid/registry",
        "postgresql://app:secret@db.invalid/registry",
        "postgresql+psycopg://app:secret@db.invalid/registry",
    ],
)
def test_db_mtls_enabled_fails_closed_for_unsupported_urls(
    db_mtls_env: None,
    db_mtls_fixture: Fixture,
    url: str,
) -> None:
    from registry_state.adapters.sqlite_store import create_engine

    with pytest.raises(Exception) as excinfo:  # noqa: BLE001
        create_engine(url)
    rendered = str(excinfo.value)
    assert "plaintext_attempt" in rendered
    _has_failure_class(rendered)
    _safe(rendered, db_mtls_fixture, url)


@pytest.mark.parametrize("sslmode", ["disable", "allow", "prefer", "require", "verify-ca"])
def test_insecure_mtls_url_policy_fails_before_engine_creation(
    db_mtls_env: None,
    db_mtls_fixture: Fixture,
    sslmode: str,
) -> None:
    from registry_state.adapters.sqlite_store import create_engine

    with pytest.raises(Exception) as excinfo:  # noqa: BLE001
        create_engine(f"{DB_URL}?sslmode={sslmode}")
    rendered = str(excinfo.value)
    assert "plaintext_attempt" in rendered
    _has_failure_class(rendered)
    _safe(rendered, db_mtls_fixture)


def test_setup_failure_produces_sanitized_audit_diagnostic_record(
    monkeypatch: pytest.MonkeyPatch,
    db_mtls_env: None,
    db_mtls_fixture: Fixture,
) -> None:
    from registry_state.adapters.sqlite_store import create_engine

    monkeypatch.setenv(
        "REGISTRY_DB_MTLS_CLIENT_KEY_PATH", str(db_mtls_fixture.base / "missing-key.pem")
    )
    with pytest.raises(Exception) as excinfo:  # noqa: BLE001
        create_engine(DB_URL)
    from mtls.db import db_mtls_failure_record

    record = db_mtls_failure_record(excinfo.value, url=DB_URL)
    assert isinstance(record, dict)
    _has_failure_class(record)
    _safe(record, db_mtls_fixture, "missing-key.pem")


def test_retry_policy_metadata_is_bounded_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    db_mtls_env: None,
    db_mtls_fixture: Fixture,
) -> None:
    from registry_state.adapters.sqlite_store import create_engine

    monkeypatch.setenv(
        "REGISTRY_DB_MTLS_CLIENT_KEY_PATH", str(db_mtls_fixture.base / "missing-key.pem")
    )
    with pytest.raises(Exception) as excinfo:  # noqa: BLE001
        create_engine(DB_URL)
    from mtls.db import db_mtls_failure_record

    record = db_mtls_failure_record(excinfo.value, url=DB_URL)
    retry = record.get("retry") or record.get("retry_policy") or {}
    assert retry.get("plaintext_fallback") is not True
    if "max_attempts" in retry:
        assert 0 <= retry["max_attempts"] <= 3
    _safe(record, db_mtls_fixture, "missing-key.pem")


def test_runtime_and_migration_connect_args_use_same_asyncpg_ssl(
    monkeypatch: pytest.MonkeyPatch,
    db_mtls_env: None,
) -> None:
    from registry_state.adapters.sqlite_store import create_engine

    runtime_ssl = _cparams(create_engine(DB_URL)).get("ssl")
    assert runtime_ssl is not None
    env_module = _load_migration_env(monkeypatch, DB_URL)
    helper = _migration_helper(env_module)
    kwargs = helper(DB_URL)
    migration_ssl = (kwargs.get("connect_args") or kwargs).get("ssl")
    assert migration_ssl is not None
    assert type(migration_ssl) is type(runtime_ssl)


def test_runtime_and_migration_disabled_noop_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    from registry_state.adapters.sqlite_store import create_engine

    monkeypatch.setenv("REGISTRY_DB_MTLS_ENABLED", "false")
    sqlite_url = "sqlite+aiosqlite:///:memory:"
    assert "ssl" not in _cparams(create_engine(sqlite_url))
    env_module = _load_migration_env(monkeypatch, sqlite_url)
    kwargs = _migration_helper(env_module)(sqlite_url)
    assert "connect_args" not in kwargs or "ssl" not in kwargs.get("connect_args", {})


def test_registry_and_migration_diagnostics_are_redacted(
    monkeypatch: pytest.MonkeyPatch,
    db_mtls_env: None,
    db_mtls_fixture: Fixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from registry_state.adapters.sqlite_store import create_engine

    monkeypatch.setenv(
        "REGISTRY_DB_MTLS_CLIENT_CERT_PATH", str(db_mtls_fixture.base / "missing-cert.pem")
    )
    with pytest.raises(Exception) as excinfo:  # noqa: BLE001
        create_engine(DB_URL)
    _has_failure_class(str(excinfo.value))
    _safe(str(excinfo.value), db_mtls_fixture, "missing-cert.pem")
    _safe(caplog.text, db_mtls_fixture, "missing-cert.pem")

    env_module = _load_migration_env(monkeypatch, DB_URL)
    with pytest.raises(Exception) as migration_exc:  # noqa: BLE001
        _migration_helper(env_module)(DB_URL)
    _has_failure_class(str(migration_exc.value))
    _safe(str(migration_exc.value), db_mtls_fixture, "missing-cert.pem")


def _migration_helper(module: ModuleType) -> Any:
    for name in (
        "build_migration_engine_kwargs",
        "build_migration_connect_kwargs",
        "create_migration_engine_kwargs",
        "get_migration_engine_kwargs",
        "_build_migration_engine_kwargs",
    ):
        helper = getattr(module, name, None)
        if helper is not None:
            return _wrap_helper(helper)
    pytest.fail("registry_state.migrations.env must expose a DB mTLS migration kwargs helper")


def _wrap_helper(helper: Any) -> Any:
    def _call(url: str) -> dict[str, Any]:
        sig = inspect.signature(helper)
        if not sig.parameters:
            return cast("dict[str, Any]", helper())
        kwargs = {
            key: value
            for key, value in {"url": url, "db_url": url, "database_url": url}.items()
            if key in sig.parameters
        }
        return cast("dict[str, Any]", helper(**kwargs) if kwargs else helper(url))

    return _call


def _load_migration_env(monkeypatch: pytest.MonkeyPatch, url: str) -> ModuleType:
    import alembic.context as alembic_context

    class FakeConfig:
        config_file_name = None
        config_ini_section = "alembic"

        def __init__(self) -> None:
            self.options = {"sqlalchemy.url": url}

        def get_main_option(self, key: str) -> str | None:
            return self.options.get(key)

        def set_main_option(self, key: str, value: str) -> None:
            self.options[key] = value

        def get_section(self, _section: str) -> dict[str, str]:
            return {"sqlalchemy.url": self.options["sqlalchemy.url"]}

    @contextmanager
    def fake_transaction() -> Any:
        yield

    monkeypatch.setattr(alembic_context, "config", FakeConfig(), raising=False)
    monkeypatch.setattr(alembic_context, "is_offline_mode", lambda: True, raising=False)
    monkeypatch.setattr(alembic_context, "configure", lambda **_kwargs: None, raising=False)
    monkeypatch.setattr(alembic_context, "begin_transaction", fake_transaction, raising=False)
    monkeypatch.setattr(alembic_context, "run_migrations", lambda: None, raising=False)
    sys.modules.pop("registry_state.migrations.env", None)
    return importlib.import_module("registry_state.migrations.env")
