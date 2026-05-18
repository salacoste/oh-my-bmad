"""Unit tests for secret_hygiene.audited_secret (Story 2.16 + 2.16-review).

Covers AC-5 scenarios + AC-1 payload validation + the 7 High / 10 Med /
14 Low review-fix patches:

* `secret.accessed` envelope construction + payload contents.
* Redaction-aware ``__repr__`` / ``__str__`` / ``__format__``.
* Pydantic serialization redaction (``model_dump`` / ``model_dump_json``).
* Sync-context (no running loop) WARNING + emission skip.
* Closed-loop ``loop.create_task`` ``RuntimeError`` graceful fallback.
* ``emit=None`` disables emission entirely.
* Empty-string / None env-var rejection.
* Bare ``MySettings()`` (without ``from_env``) loud warning.
* Emission failure does NOT propagate; secret read still succeeds.
* ``CancelledError`` / re-entrant emission contract.
* Payload NEVER carries the secret value.
* :class:`AuditedBaseSettings` env-var wrapping + repr non-leak +
  ``AliasChoices`` + ``**overrides`` forwarding + secret_name uniqueness.
* :class:`SecretAccessedPayload` model construction + serialization.
* :func:`flush_pending_emissions` helper (success + timeout paths).
* GC-anchor invariant for live emission tasks.
* Concurrent reads stress: 100 coroutines × distinct envelopes.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import logging
import time
from typing import Any, Literal

import pytest
from events.clock import FROZEN_EPOCH, FrozenClock
from events.envelope import Actor, EventEnvelope
from events.schema_registry import register, unregister_all
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from . import audited_secret as audited_secret_module
from .audited_secret import (
    AuditedBaseSettings,
    AuditedSecret,
    audited_secret_field,
    flush_pending_emissions,
)

# ---------------------------------------------------------------------------
# Schema registration — packages/ may not import services/ (NFR-M1, enforced
# by scripts/check_imports.py). When the full ``just test`` runs, other
# co-located tests under ``services/registry-state/.../test_*.py`` import
# ``registry_state.domain.event_types`` which registers the canonical
# :class:`SecretAccessedPayload`. When this test file is run in isolation
# (``uv run pytest packages/secret-hygiene/...``) those service-side test
# modules are NOT collected, so the schema is missing and
# ``EventEnvelope.create()`` raises ``EventSchemaUnknown``.
#
# Resolution (post-review-fix H7): always register the local fallback
# payload model under the same keys via try/except ValueError (xdist-safe
# AND tolerant of registry-state having registered the canonical model
# already). Then bind ``SecretAccessedPayload`` deterministically to the
# local class — this avoids the test-order-dependent class-binding bug
# the original code had (where the bound class differed depending on
# which other test files had been imported first).
# ---------------------------------------------------------------------------


class _LocalSecretAccessedPayload(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    secret_name: str = Field(min_length=1, max_length=128)
    scope: Literal["read"] = "read"


def _ensure_secret_accessed_registered() -> None:
    """Idempotently register ``secret.accessed`` for both schema versions.

    Called at module import time AND from a function-scoped autouse
    fixture so tests pass even when other co-located tests under
    ``packages/events/`` clear the registry between cases.

    Re-registering the SAME class is a no-op per the Story 2.1 schema
    registry contract; registering a DIFFERENT class for an already-
    registered key raises ``ValueError`` — caught and ignored (xdist
    race tolerance + registry-state-canonical co-existence per L23).
    """
    for _v in ("1.0.0", "1.0.1", "1.1.0"):
        # Already registered (same class — no-op) or canonical
        # registry-state class is registered. Either is fine; the
        # tests don't rely on which class is bound.
        # Story 9.7 / AC10: 1.1.0 added for envelope-level trace_id bump.
        with contextlib.suppress(ValueError):
            register("secret.accessed", _v, _LocalSecretAccessedPayload)


_ensure_secret_accessed_registered()


@pytest.fixture(autouse=True)
def _re_register_secret_accessed() -> Any:
    """Re-register ``secret.accessed`` before every test in this file."""
    _ensure_secret_accessed_registered()
    yield


# Deterministic class binding (H7): always the local class. The test
# assertions don't care which class is registered — both have identical
# field structure — but binding the local class avoids the import-order
# non-determinism the original code had via REGISTRY[...] lookup.
SecretAccessedPayload: Any = _LocalSecretAccessedPayload


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _actor() -> Actor:
    return Actor(kind="worker", id="worker-wrapper")


class _RecordingEmitter:
    """Async callable that records every envelope it receives."""

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


class _SlowEmitter:
    """Async callable that sleeps ``delay_s`` before recording the envelope."""

    def __init__(self, delay_s: float) -> None:
        self._delay_s = delay_s
        self.envelopes: list[EventEnvelope] = []

    async def __call__(self, envelope: EventEnvelope) -> None:
        await asyncio.sleep(self._delay_s)
        self.envelopes.append(envelope)


async def _drain_emissions() -> None:
    """Deterministic wait for all outstanding emission tasks to settle.

    Replaces the original three-yield ``_drain`` (which was nondeterministic
    when emit callables yielded internally). Uses ``asyncio.gather`` over
    the module-global live-task anchor.
    """
    tasks = list(audited_secret_module._live_emission_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


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
        await _drain_emissions()

        assert len(emitter.envelopes) == 1
        env = emitter.envelopes[0]
        assert env.type == "secret.accessed"
        assert env.schema_version == "1.1.0"
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
        await _drain_emissions()

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
        await _drain_emissions()

        assert len(emitter.envelopes) == 3
        assert {env.type for env in emitter.envelopes} == {"secret.accessed"}

    @pytest.mark.asyncio
    async def test_concurrent_reads_emit_distinct_envelopes(self) -> None:
        """M14: 100 coroutines × concurrent reads → 100 distinct envelopes."""
        emitter = _RecordingEmitter()
        s = AuditedSecret(
            "value-c",
            secret_name="concurrent_check",
            emit=emitter,
            actor=_actor(),
        )

        async def _read() -> str:
            return s.value

        results = await asyncio.gather(*[_read() for _ in range(100)])
        await _drain_emissions()

        assert all(r == "value-c" for r in results)
        assert len(emitter.envelopes) == 100
        event_ids = {env.event_id for env in emitter.envelopes}
        assert len(event_ids) == 100  # all distinct


# ---------------------------------------------------------------------------
# AC-2 — redaction-aware repr / format
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
        # Object's __format__ falls through to __str__ (which is __repr__).
        assert format(s, "") == "<REDACTED:api_key>"
        assert "very-secret-value-9999" not in f"{s}"
        assert "very-secret-value-9999" not in f"{s!r}"

    def test_model_dump_does_not_leak_secret(self) -> None:
        """H1: pydantic ``model_dump`` must redact via custom core schema."""

        class M(BaseModel):
            model_config = ConfigDict(arbitrary_types_allowed=True)
            secret: AuditedSecret

        s = AuditedSecret(
            "PLAINTEXT-MODEL-DUMP-LEAK",
            secret_name="anthropic_api_key",
            emit=None,
            actor=_actor(),
        )
        m = M(secret=s)
        dumped = m.model_dump()
        assert dumped == {"secret": "<REDACTED:anthropic_api_key>"}
        assert "PLAINTEXT-MODEL-DUMP-LEAK" not in repr(dumped)

    def test_model_dump_json_does_not_leak_secret(self) -> None:
        """H1: pydantic ``model_dump_json`` must redact via custom core schema."""

        class M(BaseModel):
            model_config = ConfigDict(arbitrary_types_allowed=True)
            secret: AuditedSecret

        s = AuditedSecret(
            "PLAINTEXT-JSON-LEAK-9999",
            secret_name="anthropic_api_key",
            emit=None,
            actor=_actor(),
        )
        m = M(secret=s)
        blob = m.model_dump_json()
        assert "PLAINTEXT-JSON-LEAK-9999" not in blob
        assert "<REDACTED:anthropic_api_key>" in blob


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

        with caplog.at_level(logging.WARNING, logger="secret_hygiene.audited_secret"):
            v = s.value

        assert v == "value-y"
        assert len(emitter.envelopes) == 0
        # M6: assert the WARNING record was actually captured (stdlib
        # logger now used so caplog sees it).
        assert any(
            r.name == "secret_hygiene.audited_secret"
            and r.levelno == logging.WARNING
            and ("no running event loop" in r.getMessage().lower())
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_closed_loop_create_task_does_not_crash(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """H2: ``loop.create_task`` raising ``RuntimeError`` must not break ``.value``."""
        emitter = _RecordingEmitter()
        s = AuditedSecret(
            "value-closed",
            secret_name="closed_loop_secret",
            emit=emitter,
            actor=_actor(),
        )

        running = asyncio.get_running_loop()
        original_create_task = running.create_task

        def _broken_create_task(coro: Any, *args: Any, **kwargs: Any) -> Any:
            # Close the coroutine so Python doesn't emit
            # "coroutine was never awaited" warnings.
            coro.close()
            raise RuntimeError("Event loop is closed")

        # Manually patch + restore so teardown doesn't run with the
        # broken create_task in place (asyncio runner uses create_task
        # during shutdown_asyncgens).
        running.create_task = _broken_create_task  # type: ignore[method-assign]
        try:
            with caplog.at_level(logging.WARNING, logger="secret_hygiene.audited_secret"):
                v = s.value
        finally:
            running.create_task = original_create_task  # type: ignore[method-assign]

        assert v == "value-closed"
        assert len(emitter.envelopes) == 0
        assert any(
            r.levelno == logging.WARNING
            and "create_task" in r.getMessage()
            and "closed loop" in r.getMessage().lower()
            for r in caplog.records
        )

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
            await _drain_emissions()

        assert v == "the-actual-secret"
        assert emitter.calls == 1
        # M7: assert ERROR record captured + message contains the right keyword.
        error_records = [r for r in caplog.records if r.levelname == "ERROR"]
        assert error_records, "expected an ERROR log record but none was captured"
        assert any("emission" in r.getMessage() for r in error_records)

    @pytest.mark.asyncio
    async def test_envelope_construction_failure_does_not_propagate(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Schema-registry drift must not make a secret read crash."""
        emitter = _RecordingEmitter()
        s = AuditedSecret(
            "the-actual-secret",
            secret_name="missing_schema_secret",
            emit=emitter,
            actor=_actor(),
        )

        unregister_all()
        try:
            with caplog.at_level(logging.WARNING, logger="secret_hygiene.audited_secret"):
                v = s.value
                await _drain_emissions()
        finally:
            _ensure_secret_accessed_registered()

        assert v == "the-actual-secret"
        assert emitter.envelopes == []
        # Story 9.7 PH-A6/E10: log message changed to ``audit_emission_dropped``
        # when narrowed exception (EventSchemaUnknown / ValidationError) fires.
        assert any("audit_emission_dropped" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_cancelled_error_inside_emit_is_contained(self) -> None:
        """M1: ``asyncio.CancelledError`` raised inside emit propagates to the task.

        The fire-and-forget task observes CancelledError; ``.value`` returned
        the secret already (the task hasn't run yet at the point .value
        returned).
        """
        emitter = _RaisingEmitter(asyncio.CancelledError())
        s = AuditedSecret(
            "value-cancel",
            secret_name="cancel_check",
            emit=emitter,
            actor=_actor(),
        )

        v = s.value
        # Drain — gather with return_exceptions=True so the cancelled task
        # surfaces as the exception, not a raise.
        tasks = list(audited_secret_module._live_emission_tasks)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert v == "value-cancel"
        # The task observed the CancelledError.
        assert any(isinstance(r, asyncio.CancelledError) for r in results)


# ---------------------------------------------------------------------------
# H6 — flush_pending_emissions helper
# ---------------------------------------------------------------------------


class TestFlushPendingEmissions:
    @pytest.mark.asyncio
    async def test_flush_waits_for_all_emissions(self) -> None:
        emitter = _SlowEmitter(delay_s=0.05)
        s = AuditedSecret(
            "v-flush",
            secret_name="flush_check",
            emit=emitter,
            actor=_actor(),
        )

        for _ in range(5):
            _ = s.value

        await flush_pending_emissions(timeout=2.0)
        assert len(emitter.envelopes) == 5

    @pytest.mark.asyncio
    async def test_flush_timeout_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        emitter = _SlowEmitter(delay_s=2.0)
        s = AuditedSecret(
            "v-timeout",
            secret_name="flush_timeout_check",
            emit=emitter,
            actor=_actor(),
        )

        _ = s.value

        with caplog.at_level(logging.WARNING, logger="secret_hygiene.audited_secret"):
            # Returns normally even though emission still pending.
            await flush_pending_emissions(timeout=0.05)

        assert any("did not complete within" in r.getMessage() for r in caplog.records)

        # Drain the still-pending task so the test loop teardown is clean.
        await _drain_emissions()

    @pytest.mark.asyncio
    async def test_flush_returns_immediately_when_no_pending(self) -> None:
        # No tasks scheduled → flush should be a no-op.
        await flush_pending_emissions(timeout=0.01)


# ---------------------------------------------------------------------------
# H3 / H4 — empty-string + None env-var rejection in from_env
# ---------------------------------------------------------------------------


class _DemoSettings(AuditedBaseSettings):
    """Test-only subclass declaring one audited secret field."""

    anthropic_api_key: AuditedSecret = audited_secret_field(
        "anthropic_api_key", env_var="ANTHROPIC_API_KEY"
    )


class TestFromEnvEmptyAndNone:
    def test_empty_string_env_var_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        with pytest.raises(ValueError, match="empty string"):
            _DemoSettings.from_env(emit=None, actor=_actor())

    def test_none_value_rejected(self) -> None:
        # Construct a None directly via overrides, bypassing env-vars.
        # Pre-validator's str isinstance check will not coerce None →
        # placeholder; from_env's None branch raises.
        with pytest.raises(ValueError, match=r"None|empty string"):
            _DemoSettings.from_env(emit=None, actor=_actor(), anthropic_api_key=None)


# ---------------------------------------------------------------------------
# H5 — bare MySettings() (without from_env) loud warning
# ---------------------------------------------------------------------------


class TestUnconfiguredActorWarning:
    def test_bare_construction_warns_on_value_read(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """H5: reading from a bare ``MySettings(...)`` (no from_env) warns."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        s = _DemoSettings()  # bare construction — no from_env
        # Sanity: the placeholder actor is the unconfigured sentinel.
        assert s.anthropic_api_key._actor is audited_secret_module._UNCONFIGURED_ACTOR

        with caplog.at_level(logging.WARNING, logger="secret_hygiene.audited_secret"):
            v = s.anthropic_api_key.value
            v2 = s.anthropic_api_key.value
        assert v == "x"
        assert v2 == "x"
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "audit trail INCOMPLETE" in r.getMessage()
        ]
        # Per-instance once-flag: only one warning, not two.
        assert len(warnings) == 1


# ---------------------------------------------------------------------------
# AC-3 / AC-4 — pydantic-settings integration
# ---------------------------------------------------------------------------


class TestAuditedBaseSettings:
    @pytest.mark.asyncio
    async def test_wraps_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test123")
        emitter = _RecordingEmitter()
        settings = _DemoSettings.from_env(emit=emitter, actor=_actor())

        assert isinstance(settings.anthropic_api_key, AuditedSecret)
        assert settings.anthropic_api_key.value == "test123"
        await _drain_emissions()
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
        assert "<REDACTED:anthropic_api_key>" in rendered_repr

    @pytest.mark.asyncio
    async def test_from_env_idempotent_rewrap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Calling from_env twice with different emitters rewraps cleanly."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "stable-value")
        first = _RecordingEmitter()
        settings = _DemoSettings.from_env(emit=first, actor=_actor())
        assert settings.anthropic_api_key.value == "stable-value"
        await _drain_emissions()
        assert len(first.envelopes) == 1

        second = _RecordingEmitter()
        settings2 = _DemoSettings.from_env(emit=second, actor=_actor())
        assert settings2.anthropic_api_key.value == "stable-value"
        await _drain_emissions()
        assert len(second.envelopes) == 1
        assert len(first.envelopes) == 1


# ---------------------------------------------------------------------------
# M2 — AliasChoices support in pre-validator
# ---------------------------------------------------------------------------


class _AliasChoicesSettings(AuditedBaseSettings):
    secret: AuditedSecret = audited_secret_field("alias_choice_secret")
    # Override validation_alias post-factory: easier than threading into
    # audited_secret_field. Pydantic-settings reads validation_alias for
    # env-var resolution.
    model_config = AuditedBaseSettings.model_config


_AliasChoicesSettings.model_fields["secret"].validation_alias = AliasChoices(
    "ALIAS_FOO", "ALIAS_BAR"
)
_AliasChoicesSettings.model_rebuild(force=True)


class TestAliasChoicesSupport:
    @pytest.mark.asyncio
    async def test_alias_choices_picks_up_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALIAS_FOO", raising=False)
        monkeypatch.setenv("ALIAS_BAR", "alias-value")

        emitter = _RecordingEmitter()
        settings = _AliasChoicesSettings.from_env(emit=emitter, actor=_actor())
        assert settings.secret.value == "alias-value"
        await _drain_emissions()
        assert len(emitter.envelopes) == 1


# ---------------------------------------------------------------------------
# L18 — conflict detection in pre-validator
# ---------------------------------------------------------------------------


class TestConflictDetection:
    def test_conflicting_alias_keys_raises(self) -> None:
        """Both field-name and alias supplied with different values → raise."""
        # Use **kwargs dict-splat to satisfy mypy (the alias key isn't a
        # known declared field name, so direct kwargs trip mypy's
        # call-arg check).
        kwargs: dict[str, Any] = {
            "anthropic_api_key": "value-a",
            "ANTHROPIC_API_KEY": "value-b",
        }
        with pytest.raises(ValidationError) as excinfo:
            _DemoSettings(**kwargs)
        # The ValueError nested inside ValidationError carries our message.
        assert "conflicting values" in str(excinfo.value)


# ---------------------------------------------------------------------------
# M3 — sentinel marker prevents foreign-extra wrapping
# ---------------------------------------------------------------------------


class TestSentinelMarker:
    def test_foreign_extra_without_marker_not_wrapped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A field carrying ``audited_secret_name`` extra WITHOUT the marker
        must NOT be treated as audited.
        """
        from pydantic import Field as PydField

        class ForeignSettings(AuditedBaseSettings):
            real: AuditedSecret = audited_secret_field("real_secret", env_var="REAL_SECRET")
            # Foreign field with extra-key collision but no marker.
            decoy: str = PydField(
                default="hello",
                json_schema_extra={"audited_secret_name": "decoy_should_not_wrap"},
            )

        monkeypatch.setenv("REAL_SECRET", "xyz")
        instance = ForeignSettings()
        # decoy is NOT wrapped (still a plain str).
        assert instance.decoy == "hello"
        assert not isinstance(instance.decoy, AuditedSecret)
        # And `real` IS wrapped.
        assert isinstance(instance.real, AuditedSecret)


# ---------------------------------------------------------------------------
# M4 — _UNSET sentinel for default
# ---------------------------------------------------------------------------


class TestDefaultSentinel:
    def test_default_unset_is_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class S(AuditedBaseSettings):
            field_x: AuditedSecret = audited_secret_field(
                "field_x_secret", env_var="FIELD_X_SECRET"
            )

        monkeypatch.delenv("FIELD_X_SECRET", raising=False)
        with pytest.raises(ValidationError):
            S()  # required, no default → ValidationError

    def test_default_none_makes_optional(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class S(AuditedBaseSettings):
            field_y: AuditedSecret | None = audited_secret_field(
                "field_y_secret", env_var="FIELD_Y_SECRET", default=None
            )

        monkeypatch.delenv("FIELD_Y_SECRET", raising=False)
        s = S()
        assert s.field_y is None


# ---------------------------------------------------------------------------
# M8 — secret_name uniqueness in subclass
# ---------------------------------------------------------------------------


class TestSecretNameUniqueness:
    def test_duplicate_secret_name_raises_at_class_def(self) -> None:
        with pytest.raises(ValueError, match="unique secret_name"):

            class Dupe(AuditedBaseSettings):  # noqa: F841
                a: AuditedSecret = audited_secret_field("dupe", env_var="A")
                b: AuditedSecret = audited_secret_field("dupe", env_var="B")


# ---------------------------------------------------------------------------
# M10 — re-entrant emission guard
# ---------------------------------------------------------------------------


class TestReentrantEmissionGuard:
    @pytest.mark.asyncio
    async def test_emit_reading_secret_b_does_not_recurse(self) -> None:
        """Emit callable that reads a second AuditedSecret must not fan out."""
        outer_envelopes: list[EventEnvelope] = []

        # secret_b is read INSIDE secret_a's emit — its emission must be skipped.
        secret_b = AuditedSecret(
            "value-b",
            secret_name="reentrant_b",
            emit=_RecordingEmitter(),
            actor=_actor(),
        )

        async def inner_emit(envelope: EventEnvelope) -> None:
            outer_envelopes.append(envelope)
            # Read secret_b — this should NOT schedule a new emission
            # because we're inside an emission already.
            _ = secret_b.value

        secret_a = AuditedSecret(
            "value-a",
            secret_name="reentrant_a",
            emit=inner_emit,
            actor=_actor(),
        )

        _ = secret_a.value
        await _drain_emissions()

        # Exactly 1 envelope: secret_a's. secret_b's was skipped.
        assert len(outer_envelopes) == 1
        payload = outer_envelopes[0].payload
        if isinstance(payload, dict):
            assert payload["secret_name"] == "reentrant_a"
        else:
            payload_any: Any = payload
            assert payload_any.secret_name == "reentrant_a"


# ---------------------------------------------------------------------------
# L6 / L19 / L20 — secret_name validation
# ---------------------------------------------------------------------------


class TestSecretNameValidation:
    def test_audited_secret_rejects_bad_secret_name(self) -> None:
        with pytest.raises(ValueError, match="secret_name"):
            AuditedSecret(
                "x",
                secret_name="bad name with spaces",
                emit=None,
                actor=_actor(),
            )

    def test_audited_secret_field_rejects_bad_secret_name(self) -> None:
        with pytest.raises(ValueError, match="secret_name"):
            audited_secret_field("bad name", env_var="X")

    def test_audited_secret_rejects_oversized_secret_name(self) -> None:
        with pytest.raises(ValueError, match="secret_name"):
            AuditedSecret(
                "x",
                secret_name="a" * 129,
                emit=None,
                actor=_actor(),
            )


# ---------------------------------------------------------------------------
# L15 — direct construction with empty value allowed
# ---------------------------------------------------------------------------


class TestDirectConstructionEmpty:
    @pytest.mark.asyncio
    async def test_audited_secret_value_can_be_empty_string(self) -> None:
        emitter = _RecordingEmitter()
        s = AuditedSecret(
            "",
            secret_name="empty_ok",
            emit=emitter,
            actor=_actor(),
        )
        assert s.value == ""
        await _drain_emissions()
        assert len(emitter.envelopes) == 1


# ---------------------------------------------------------------------------
# L16 — from_env forwards **overrides
# ---------------------------------------------------------------------------


class _MultiFieldSettings(AuditedBaseSettings):
    api_key: AuditedSecret = audited_secret_field("api_key", env_var="THE_API_KEY")
    extra_str: str = "default-extra"


class TestFromEnvOverrides:
    @pytest.mark.asyncio
    async def test_overrides_propagate_to_cls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("THE_API_KEY", "from-env-value")
        settings = _MultiFieldSettings.from_env(
            emit=None, actor=_actor(), extra_str="override-value"
        )
        assert settings.extra_str == "override-value"
        assert settings.api_key.value == "from-env-value"


# ---------------------------------------------------------------------------
# L24 — clock=None default uses real SystemClock
# ---------------------------------------------------------------------------


class TestSystemClockDefault:
    @pytest.mark.asyncio
    async def test_value_with_default_system_clock_emits_increasing_monotonic(
        self,
    ) -> None:
        emitter = _RecordingEmitter()
        s = AuditedSecret(
            "v",
            secret_name="sysclock",
            emit=emitter,
            actor=_actor(),
            clock=None,  # explicit default
        )
        _ = s.value
        # Tiny sleep to guarantee monotonic_ns advances.
        await asyncio.sleep(0.001)
        _ = s.value
        await _drain_emissions()

        assert len(emitter.envelopes) == 2
        env1, env2 = emitter.envelopes
        assert env2.emitted_at_monotonic_ns > env1.emitted_at_monotonic_ns


# ---------------------------------------------------------------------------
# L25 — _live_emission_tasks anchor survives gc.collect
# ---------------------------------------------------------------------------


class TestLiveEmissionTasksAnchor:
    @pytest.mark.asyncio
    async def test_live_emission_tasks_anchor_survives_gc(self) -> None:
        """L25: scheduled emission must complete even after a GC sweep."""
        emitter = _SlowEmitter(delay_s=0.05)
        s = AuditedSecret(
            "v-gc",
            secret_name="gc_check",
            emit=emitter,
            actor=_actor(),
        )

        _ = s.value
        # Force GC mid-flight; the WeakSet anchor should keep the task alive.
        gc.collect()
        time.sleep(0.01)
        gc.collect()

        await flush_pending_emissions(timeout=2.0)
        assert len(emitter.envelopes) == 1


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
        await _drain_emissions()

        assert len(emitter.envelopes) == 1
        env = emitter.envelopes[0]
        assert env.actor.kind == "system"
        assert env.actor.id == "registry-api"
        assert env.emitted_at_monotonic_ns == 12345
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
        await _drain_emissions()
        assert seen == ["secret.accessed"]
