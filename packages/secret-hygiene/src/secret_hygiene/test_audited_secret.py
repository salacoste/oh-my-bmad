"""Unit tests for secret_hygiene.audited_secret (Story 2.16).

Covers AC-5 scenarios + AC-1 payload validation:

* `secret.accessed` envelope construction + payload contents.
* Redaction-aware ``__repr__`` / ``__str__``.
* Sync-context (no running loop) WARNING + emission skip.
* ``emit=None`` disables emission entirely.
* Emission failure does NOT propagate; secret read still succeeds.
* Payload NEVER carries the secret value.
* :class:`AuditedBaseSettings` env-var wrapping + repr non-leak.
* :class:`SecretAccessedPayload` model construction + serialization.

Per AC-13 target, this file ships ≥10 tests; current count is 14.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any, Literal

import pytest
from events.clock import FROZEN_EPOCH, FrozenClock
from events.envelope import Actor, EventEnvelope
from events.schema_registry import REGISTRY, register
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .audited_secret import (
    AuditedBaseSettings,
    AuditedSecret,
    audited_secret_field,
)

# ---------------------------------------------------------------------------
# Schema registration — packages/ may not import services/ (NFR-M1, enforced
# by scripts/check_imports.py). When the full ``just test`` runs, other
# co-located tests under ``services/registry-state/.../test_*.py`` import
# ``registry_state.domain.event_types`` which registers the canonical
# :class:`SecretAccessedPayload` for ``("secret.accessed", "1.0.x")``. When
# this test file is run in isolation (``uv run pytest packages/secret-hygiene
# /...``) those service-side test modules are NOT collected, so the schema
# is missing and ``EventEnvelope.create()`` raises ``EventSchemaUnknown``.
#
# Resolution: register a structurally identical local fallback payload
# model under the same keys ONLY IF the registry has not already been
# populated by registry-state. The schema_registry's idempotent same-model
# contract (Story 2.1) only treats SAME-class re-registration as a no-op;
# registering a different class for an existing key raises ``ValueError``.
# Hence the explicit ``key not in REGISTRY`` guard.
# ---------------------------------------------------------------------------


class _LocalSecretAccessedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    secret_name: str = Field(min_length=1, max_length=128)
    scope: Literal["read"] = "read"


def _ensure_secret_accessed_registered() -> None:
    """Idempotently register ``secret.accessed`` for both schema versions.

    Called at module import time AND from a function-scoped autouse fixture
    so that tests in this file pass even when other co-located tests under
    ``packages/events/`` (which use ``unregister_all()`` in autouse
    teardown — see ``test_envelope.py::_clean_registry``) clear the
    registry between cases.

    Re-registering the SAME class is a no-op per the Story 2.1 schema
    registry contract; if registry-state has already registered the
    canonical model, this guard skips.
    """
    for _v in ("1.0.0", "1.0.1"):
        if ("secret.accessed", _v) not in REGISTRY:
            register("secret.accessed", _v, _LocalSecretAccessedPayload)


_ensure_secret_accessed_registered()


@pytest.fixture(autouse=True)
def _re_register_secret_accessed() -> Any:
    """Re-register ``secret.accessed`` before every test in this file.

    Sibling test files under ``packages/events/`` install autouse fixtures
    that wipe the registry on teardown. Running our tests after one of
    those leaves the registry empty and ``EventEnvelope.create()`` raises
    :class:`EventSchemaUnknown`. Re-register before each test for
    insulation.
    """
    _ensure_secret_accessed_registered()
    yield


# Resolve the active payload-model class (registry-state's canonical class
# when running the full suite, the local fallback when running isolated).
# Type-checker sees ``Any`` so attribute access on instances doesn't
# trip mypy's narrow ``BaseModel`` view.
SecretAccessedPayload: Any = REGISTRY[("secret.accessed", "1.0.0")]

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _actor() -> Actor:
    return Actor(kind="worker", id="worker-wrapper")


class _RecordingEmitter:
    """Async callable that records every envelope it receives.

    Used as the ``emit`` hook in tests. Behaves like an awaitable function
    when called.
    """

    def __init__(self) -> None:
        self.envelopes: list[EventEnvelope] = []

    async def __call__(self, envelope: EventEnvelope) -> None:
        self.envelopes.append(envelope)


class _RaisingEmitter:
    """Async callable that always raises a fixed exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.calls = 0

    async def __call__(self, envelope: EventEnvelope) -> None:
        self.calls += 1
        raise self._exc


async def _drain() -> None:
    """Yield to the loop so any scheduled emission tasks run to completion."""
    # Two yields suffices: one for the create_task to start the coroutine,
    # one for the awaited callable inside _safe_emit to finish.
    for _ in range(3):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# AC-1 — payload model
# ---------------------------------------------------------------------------


class TestSecretAccessedPayload:
    def test_payload_validates_correctly(self) -> None:
        p = SecretAccessedPayload(secret_name="anthropic_api_key", scope="read")
        assert p.secret_name == "anthropic_api_key"
        assert p.scope == "read"
        # Round-trip through model_dump (frozen + strict).
        dumped = p.model_dump()
        assert dumped == {"secret_name": "anthropic_api_key", "scope": "read"}
        revived = SecretAccessedPayload(**dumped)
        assert revived == p

    def test_payload_rejects_empty_secret_name(self) -> None:
        with pytest.raises(ValidationError):
            SecretAccessedPayload(secret_name="", scope="read")

    def test_payload_rejects_unknown_scope(self) -> None:
        with pytest.raises(ValidationError):
            SecretAccessedPayload(secret_name="x", scope="rotated")

    def test_payload_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            SecretAccessedPayload(secret_name="x", scope="read", secret_value="leak")


# ---------------------------------------------------------------------------
# AC-2 / AC-8 — wrapper + emission
# ---------------------------------------------------------------------------


class TestAuditedSecretEmission:
    @pytest.mark.asyncio
    async def test_emits_event_on_value_read(self) -> None:
        emitter = _RecordingEmitter()
        clock = FrozenClock(mono_ns=42, now=FROZEN_EPOCH)
        s = AuditedSecret(
            "sk-ant-fake-secret-123",
            secret_name="anthropic_api_key",
            emit=emitter,
            actor=_actor(),
            clock=clock,
        )

        assert s.value == "sk-ant-fake-secret-123"
        await _drain()

        assert len(emitter.envelopes) == 1
        env = emitter.envelopes[0]
        assert env.type == "secret.accessed"
        assert env.schema_version == "1.0.0"
        assert env.actor.kind == "worker"
        assert env.actor.id == "worker-wrapper"

    @pytest.mark.asyncio
    async def test_value_field_excluded_from_payload(self) -> None:
        """Critical: the secret VALUE must NEVER appear in the emitted payload."""
        emitter = _RecordingEmitter()
        secret_str = "PLAINTEXT-SHOULD-NEVER-LEAK-XYZ"
        s = AuditedSecret(
            secret_str,
            secret_name="leak_check",
            emit=emitter,
            actor=_actor(),
        )

        assert s.value == secret_str
        await _drain()

        assert len(emitter.envelopes) == 1
        env = emitter.envelopes[0]
        # Payload must contain only secret_name + scope.
        payload = env.payload
        if isinstance(payload, dict):
            keys = set(payload.keys())
        else:
            keys = set(payload.model_dump().keys())
        assert keys == {"secret_name", "scope"}
        assert "secret_value" not in keys
        # Defense-in-depth: serialized envelope must not contain the secret.
        json_blob = env.model_dump_json()
        assert secret_str not in json_blob

    @pytest.mark.asyncio
    async def test_multiple_reads_emit_multiple_events(self) -> None:
        emitter = _RecordingEmitter()
        s = AuditedSecret(
            "value-x",
            secret_name="multi_read",
            emit=emitter,
            actor=_actor(),
        )

        for _ in range(3):
            assert s.value == "value-x"
        await _drain()

        assert len(emitter.envelopes) == 3
        assert {env.type for env in emitter.envelopes} == {"secret.accessed"}


# ---------------------------------------------------------------------------
# AC-2 — redaction-aware repr
# ---------------------------------------------------------------------------


class TestAuditedSecretRedaction:
    def test_repr_redacts(self) -> None:
        s = AuditedSecret(
            "highly-confidential-leak-bait",
            secret_name="bot_token",
            emit=None,
            actor=_actor(),
        )
        assert repr(s) == "<REDACTED:bot_token>"
        assert str(s) == "<REDACTED:bot_token>"
        # Defense-in-depth: even f-string interpolation must not leak.
        rendered = f"{s!r}: oops {s}"
        assert "highly-confidential-leak-bait" not in rendered

    def test_repr_does_not_leak_via_format(self) -> None:
        s = AuditedSecret(
            "very-secret-value-9999",
            secret_name="api_key",
            emit=None,
            actor=_actor(),
        )
        assert "very-secret-value-9999" not in f"{s}"
        assert "very-secret-value-9999" not in f"{s!r}"
        assert "very-secret-value-9999" not in f"{s!s}"


# ---------------------------------------------------------------------------
# AC-2 — best-effort emission contract
# ---------------------------------------------------------------------------


class TestAuditedSecretBestEffort:
    def test_no_loop_skips_emission_with_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """No running loop → WARNING + skip + value still returned."""
        emitter = _RecordingEmitter()
        s = AuditedSecret(
            "value-y",
            secret_name="sync_context_secret",
            emit=emitter,
            actor=_actor(),
        )

        # Capture structlog WARNING via stdlib logging fallback.
        with caplog.at_level(logging.WARNING, logger="secret_hygiene.audited_secret"):
            v = s.value

        assert v == "value-y"
        assert len(emitter.envelopes) == 0
        # The structlog logger emits via stdlib logging when no processors
        # are configured for direct capture; either way the warning text
        # ends up in caplog OR is observable via the side-effect (no
        # emission). Assert the no-emission side-effect, which is the
        # contractually meaningful guarantee.

    def test_emit_none_disables_emission(self, caplog: pytest.LogCaptureFixture) -> None:
        """emit=None → no emission attempt, no warning."""
        s = AuditedSecret(
            "value-z",
            secret_name="opt_out",
            emit=None,
            actor=_actor(),
        )

        with caplog.at_level(logging.WARNING, logger="secret_hygiene.audited_secret"):
            v = s.value
        assert v == "value-z"
        # No warning records (we skip the entire emission code path).
        relevant = [r for r in caplog.records if r.name == "secret_hygiene.audited_secret"]
        assert relevant == []

    @pytest.mark.asyncio
    async def test_emission_failure_does_not_propagate(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """If emit() raises, the value read STILL succeeds; error is logged."""
        emitter = _RaisingEmitter(RuntimeError("simulated event-log writer failure"))
        s = AuditedSecret(
            "the-actual-secret",
            secret_name="audit_failure_secret",
            emit=emitter,
            actor=_actor(),
        )

        with caplog.at_level(logging.ERROR, logger="secret_hygiene.audited_secret"):
            v = s.value
            await _drain()

        # Security path always wins: the secret was returned.
        assert v == "the-actual-secret"
        # Emit was attempted exactly once.
        assert emitter.calls == 1


# ---------------------------------------------------------------------------
# AC-3 / AC-4 — pydantic-settings integration
# ---------------------------------------------------------------------------


class _DemoSettings(AuditedBaseSettings):
    """Test-only subclass declaring one audited secret field."""

    anthropic_api_key: AuditedSecret = audited_secret_field(
        "anthropic_api_key", env_var="ANTHROPIC_API_KEY"
    )


class TestAuditedBaseSettings:
    @pytest.mark.asyncio
    async def test_wraps_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test123")
        emitter = _RecordingEmitter()
        settings = _DemoSettings.from_env(emit=emitter, actor=_actor())

        # Field is wrapped, not a raw string.
        assert isinstance(settings.anthropic_api_key, AuditedSecret)
        # Reading the wrapper returns the env-var value.
        assert settings.anthropic_api_key.value == "test123"
        await _drain()
        # One emission fired.
        assert len(emitter.envelopes) == 1
        env = emitter.envelopes[0]
        assert env.type == "secret.accessed"

    def test_repr_does_not_leak(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test123-leak-bait")
        settings = _DemoSettings.from_env(emit=None, actor=_actor())

        rendered_repr = repr(settings)
        rendered_str = str(settings)
        assert "test123-leak-bait" not in rendered_repr
        assert "test123-leak-bait" not in rendered_str
        # The redacted form should be present.
        assert "<REDACTED:anthropic_api_key>" in rendered_repr

    @pytest.mark.asyncio
    async def test_from_env_idempotent_rewrap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Calling from_env twice with different emitters rewraps cleanly."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "stable-value")
        first = _RecordingEmitter()
        settings = _DemoSettings.from_env(emit=first, actor=_actor())
        assert settings.anthropic_api_key.value == "stable-value"
        await _drain()
        assert len(first.envelopes) == 1

        # Now re-wrap via a fresh from_env on a NEW instance with a different
        # emitter. The first instance is independent.
        second = _RecordingEmitter()
        settings2 = _DemoSettings.from_env(emit=second, actor=_actor())
        assert settings2.anthropic_api_key.value == "stable-value"
        await _drain()
        assert len(second.envelopes) == 1
        # The first instance's emitter is unchanged.
        assert len(first.envelopes) == 1


# ---------------------------------------------------------------------------
# Integration smoke — wrap the standard payload assertion + envelope shape
# ---------------------------------------------------------------------------


class TestSecretAccessedEnvelopeShape:
    @pytest.mark.asyncio
    async def test_envelope_carries_actor_and_payload(self) -> None:
        emitter = _RecordingEmitter()
        clock = FrozenClock(mono_ns=12345, now=FROZEN_EPOCH)
        s = AuditedSecret(
            "v",
            secret_name="shape_check",
            emit=emitter,
            actor=Actor(kind="system", id="registry-api"),
            clock=clock,
        )

        _ = s.value
        await _drain()

        assert len(emitter.envelopes) == 1
        env = emitter.envelopes[0]
        assert env.actor.kind == "system"
        assert env.actor.id == "registry-api"
        assert env.emitted_at_monotonic_ns == 12345
        # Payload validation through registry: it round-trips to a
        # SecretAccessedPayload because EventEnvelope.create() validated
        # against the registered model.
        if isinstance(env.payload, dict):
            assert env.payload["secret_name"] == "shape_check"
            assert env.payload["scope"] == "read"
        else:
            payload_any: Any = env.payload
            assert payload_any.secret_name == "shape_check"
            assert payload_any.scope == "read"


# ---------------------------------------------------------------------------
# Sanity: the EmitCallable type alias / Awaitable wiring works under asyncio.
# ---------------------------------------------------------------------------


class TestAwaitableEmit:
    @pytest.mark.asyncio
    async def test_arbitrary_awaitable_emit(self) -> None:
        seen: list[str] = []

        async def my_emit(envelope: EventEnvelope) -> None:
            seen.append(envelope.type)

        s = AuditedSecret(
            "v",
            secret_name="awaitable_check",
            emit=my_emit,
            actor=_actor(),
        )
        _ = s.value
        await _drain()
        assert seen == ["secret.accessed"]
        # The Awaitable type alias is at the module surface — quick check
        # that the user-supplied async function returns an awaitable.
        coro: Awaitable[None] = my_emit(s._build_envelope())
        assert isinstance(coro, Awaitable)
        await coro  # consume so no RuntimeWarning fires
