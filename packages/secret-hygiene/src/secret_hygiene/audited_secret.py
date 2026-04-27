"""AuditedSecret + AuditedBaseSettings — secret access audit emission (Story 2.16).

This module ships the **infrastructure** for FR42 / NFR-S3:

* :class:`AuditedSecret` wraps a single secret value and emits a typed
  ``secret.accessed`` event on every read of the ``.value`` property.
  The wrapper redacts under ``repr()`` / ``str()`` so accidental logging
  prints ``<REDACTED:secret_name>`` rather than the plaintext.

* :class:`AuditedBaseSettings` extends :class:`pydantic_settings.BaseSettings`
  with a :py:meth:`~AuditedBaseSettings.from_env` factory that wraps every
  field declared via :func:`audited_secret_field` in an :class:`AuditedSecret`
  carrying caller-provided ``emit`` + ``actor`` audit hooks. Subclasses get
  free audit emission on every secret read, with no per-service plumbing
  beyond the ``from_env(...)`` call at startup.

Story 2.16 ships infrastructure only — no existing service is integrated
yet. Wiring lands in follow-up stories (worker-wrapper Anthropic key
read, telegram-gateway bot-token read, etc. — see Story 2.16 Dev Notes).

Best-effort emission contract (`AuditedSecret.value`)
-----------------------------------------------------

The ``.value`` property is **synchronous** by design (Python attribute
access semantics; Pydantic field access in subclassed BaseSettings).
The ``emit`` callable is async, so emission is scheduled fire-and-forget
via ``asyncio.get_running_loop().create_task(emit(envelope))``:

* **Async context (running loop present)**: emission scheduled on the
  loop. The ``.value`` read returns immediately; the audit event fires
  asynchronously. Failures inside the scheduled task are swallowed +
  logged (security path takes precedence).
* **Sync context (no running loop)**: WARNING logged via the
  module-level ``structlog`` logger, emission silently skipped, value
  returned unchanged. Acceptable: sync-only contexts (e.g.,
  config-validation scripts) don't have access to a running
  :class:`EventLogWriter` anyway.
* **emit=None**: emission entirely disabled. No warning. Used by
  test contexts and operator-CLI sync scripts that explicitly opt
  out of audit.

The audit path **never** prevents the secret read from succeeding —
returning the secret is always the priority; audit is best-effort.

Actor.kind convention (post-Story 2.10 review)
----------------------------------------------

The ``Actor.kind`` field is a Literal, not a free-form string.
Story 2.10's review identified that the canonical set is::

    operator | orchestrator | worker | system | clawhip

Story 2.16's spec scenario uses ``kind="service"`` which is **not** in
the Literal — that is a known spec typo (AC-7). The correct mapping
per service / context:

* worker-wrapper reading a secret      → ``Actor(kind="worker", id=…)``
* registry-api / registry-state reads  → ``Actor(kind="system",   id=…)``
* telegram-gateway reads               → ``Actor(kind="system",   id=…)``
* operator-initiated reads (CLI/UI)    → ``Actor(kind="operator", id=…)``

The specific identity is carried on ``Actor.id`` (e.g.
``"worker-wrapper"``, ``"telegram-gateway"``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog
from events.clock import Clock, SystemClock
from events.envelope import Actor, EventEnvelope
from events.ids import new_event_id, new_request_id
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from typing import Self

__all__ = [
    "AuditedBaseSettings",
    "AuditedSecret",
    "audited_secret_field",
]

# Type alias for the (async) emission callable. The wrapper accepts ``None``
# to disable emission entirely (sync-only test/CLI contexts).
EmitCallable = Callable[[EventEnvelope], Awaitable[None]]

_log = structlog.get_logger("secret_hygiene.audited_secret")


class AuditedSecret:
    """Wrapper that emits ``secret.accessed`` on each value read.

    Construct with the secret value plus an async ``emit`` callable; expose
    a synchronous ``.value`` property that schedules an emission then
    returns the value. ``__repr__`` / ``__str__`` redact to
    ``"<REDACTED:secret_name>"`` so the wrapper is safe to log
    accidentally.

    The wrapper is **immutable in spirit**: ``__slots__`` blocks attribute
    addition; assigning to existing slots after construction is not
    prevented by Python but is contractually forbidden — callers should
    treat instances as read-only.

    Constructor injection of :class:`Clock` is optional (defaults to
    :class:`SystemClock`); test contexts may pass a :class:`FrozenClock`
    or :class:`TickingClock` for deterministic envelope IDs / timestamps.

    Args:
        value:        The plaintext secret. Stored on ``self._value`` and
                      returned by ``.value``. NEVER serialized into the
                      emitted event payload.
        secret_name:  Stable identifier used by the payload + repr.
                      Must satisfy
                      :class:`registry_state.domain.event_types.SecretAccessedPayload`'s
                      ``secret_name`` constraints (1..128 chars).
        emit:         Async callable that persists an :class:`EventEnvelope`
                      (typically ``EventLogWriter.append``). Pass ``None``
                      to disable emission entirely.
        actor:        Identity recorded on every emitted envelope. Reused
                      across all reads of this secret instance.
        clock:        Optional :class:`Clock` for deterministic IDs +
                      timestamps. Defaults to :class:`SystemClock`.
    """

    __slots__ = ("_actor", "_clock", "_emit", "_secret_name", "_value")

    def __init__(
        self,
        value: str,
        *,
        secret_name: str,
        emit: EmitCallable | None,
        actor: Actor,
        clock: Clock | None = None,
    ) -> None:
        if not secret_name:
            raise ValueError("AuditedSecret.secret_name must be non-empty")
        if len(secret_name) > 128:
            raise ValueError(
                f"AuditedSecret.secret_name must be <= 128 chars (got {len(secret_name)})"
            )
        self._value = value
        self._secret_name = secret_name
        self._emit = emit
        self._actor = actor
        self._clock = clock if clock is not None else SystemClock()

    @property
    def value(self) -> str:
        """Read the secret. Best-effort emission of ``secret.accessed``.

        Behavior matrix (see module docstring for full rationale):

        * ``emit=None``     → no emission, no warning, return value.
        * Running loop      → schedule emission as fire-and-forget task,
                              return value immediately.
        * No running loop   → log WARNING, skip emission, return value.

        Emission failures are logged but **never** propagate; the secret
        read always succeeds.
        """
        if self._emit is not None:
            self._schedule_emission()
        return self._value

    def _schedule_emission(self) -> None:
        """Build envelope + schedule fire-and-forget emit on the running loop.

        Splits out so subclasses / tests can override scheduling without
        rewriting the value-read path.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _log.warning(
                "secret.accessed emission skipped — no running event loop",
                secret_name=self._secret_name,
                actor_kind=self._actor.kind,
                actor_id=self._actor.id,
            )
            return

        envelope = self._build_envelope()
        # Type narrowing: _schedule_emission is only called when emit is not None
        emit = self._emit
        assert emit is not None  # noqa: S101 — invariant of the call site
        task = loop.create_task(self._safe_emit(emit, envelope))
        # Hold a reference on the running loop so the task isn't GC'd
        # mid-flight (asyncio's create_task only weak-refs the task).
        _live_emission_tasks.add(task)
        task.add_done_callback(_live_emission_tasks.discard)

    def _build_envelope(self) -> EventEnvelope:
        """Construct the canonical ``secret.accessed`` envelope.

        ``EventEnvelope.create()`` form is intentional — it routes through
        the schema registry (Story 2.1) and validates the payload against
        the :class:`SecretAccessedPayload` model registered in
        :mod:`registry_state.domain.event_types`. The
        ``check_event_registry`` gate scans for direct
        ``EventEnvelope(...)`` construction and ``<receiver>.emit(...)``
        patterns; ``EventEnvelope.create()`` is outside the scanner's
        scope (vacuously green per Story 2.10's pattern, AC-12).
        """
        return EventEnvelope.create(
            event_id=new_event_id(clock=self._clock),
            schema_version="1.0.0",
            type="secret.accessed",
            emitted_at=self._clock.now(),
            emitted_at_monotonic_ns=self._clock.monotonic_ns(),
            actor=self._actor,
            payload={"secret_name": self._secret_name, "scope": "read"},
            request_id=new_request_id(clock=self._clock),
        )

    @staticmethod
    async def _safe_emit(emit: EmitCallable, envelope: EventEnvelope) -> None:
        """Run *emit* and swallow + log any exception.

        Audit emission failures must never crash the caller. Log at
        ``error`` level so the failure is observable; the secret-read
        consumer already returned successfully by the time this runs.
        """
        try:
            await emit(envelope)
        except Exception as exc:  # noqa: BLE001 — audit must not propagate
            _log.error(
                "secret.accessed emission failed",
                event_id=envelope.event_id,
                secret_name=envelope.payload.get("secret_name")
                if isinstance(envelope.payload, dict)
                else getattr(envelope.payload, "secret_name", None),
                error=str(exc),
                error_type=type(exc).__name__,
            )

    def __repr__(self) -> str:
        return f"<REDACTED:{self._secret_name}>"

    __str__ = __repr__


# Module-level set holding live emission tasks so they survive long enough
# to complete. asyncio's create_task only holds a weak reference; without
# this anchor a fire-and-forget task can be garbage-collected before its
# coroutine awaits the writer. The done-callback in `_schedule_emission`
# discards the entry once the task settles.
_live_emission_tasks: set[asyncio.Task[None]] = set()


# ---------------------------------------------------------------------------
# Pydantic-settings integration
# ---------------------------------------------------------------------------


def audited_secret_field(
    secret_name: str,
    *,
    env_var: str | None = None,
    default: str | None = None,
) -> Any:
    """Return a Pydantic Field wired for AuditedSecret post-construction wrapping.

    Use inside an :class:`AuditedBaseSettings` subclass::

        class MySettings(AuditedBaseSettings):
            anthropic_api_key: AuditedSecret = audited_secret_field(
                "anthropic_api_key", env_var="ANTHROPIC_API_KEY"
            )

    The factory stashes ``secret_name`` in the field's ``json_schema_extra``
    so :py:meth:`AuditedBaseSettings.from_env` (and the class's
    ``model_validator(mode='before')``) can discover which fields are
    audited at construction time. ``env_var`` overrides the default
    env-var name (otherwise pydantic-settings derives it from the
    field name uppercased). ``default`` sets a fallback value for when
    the env-var is unset; pass ``None`` to make the field required.

    Returns:
        A :class:`pydantic.Field` whose metadata is consumed by
        :class:`AuditedBaseSettings`.
    """
    extra: dict[str, Any] = {"audited_secret_name": secret_name}
    kwargs: dict[str, Any] = {
        "json_schema_extra": extra,
    }
    if env_var is not None:
        kwargs["validation_alias"] = env_var
    if default is None:
        # Required field — no default. pydantic-settings will raise on
        # missing env-var.
        return Field(**kwargs)
    return Field(default=default, **kwargs)


_PLACEHOLDER_ACTOR = Actor(kind="system", id="audited-base-settings-placeholder")


class AuditedBaseSettings(BaseSettings):
    """:class:`BaseSettings` subclass supporting :class:`AuditedSecret` fields.

    Subclasses declare secrets via :func:`audited_secret_field` typed as
    ``AuditedSecret``. On :py:meth:`from_env`, each declared secret is:

      1. Resolved from env-vars by pydantic-settings (raw string).
      2. Wrapped in an :class:`AuditedSecret` carrying the caller-supplied
         ``emit`` + ``actor`` (and optional ``clock``).
      3. Reassigned onto the instance via ``object.__setattr__`` (the
         model itself is not frozen, but the rewrap path bypasses any
         Pydantic validators that would reject the wrapper type).

    Pydantic typing limitation: declaring a field as ``AuditedSecret``
    works at runtime (we treat it as ``Any`` during initial validation
    because pydantic-settings cannot natively coerce env-vars to
    :class:`AuditedSecret`) and is rewrapped post-construction. The
    ``# type: ignore`` annotations on the ``arbitrary_types_allowed``
    config plus the field type are intentional — pydantic-settings'
    type-checker is overly strict for non-Pydantic types.

    Example::

        class MySettings(AuditedBaseSettings):
            anthropic_api_key: AuditedSecret = audited_secret_field(
                "anthropic_api_key", env_var="ANTHROPIC_API_KEY"
            )

        settings = MySettings.from_env(emit=writer.append, actor=Actor(...))
        # ... later, in async context:
        key = settings.anthropic_api_key.value  # emits secret.accessed
    """

    model_config = SettingsConfigDict(
        arbitrary_types_allowed=True,
        # extra="forbid" — inherited from BaseSettings default; not set
        # here so subclasses retain pydantic-settings' env-var
        # resolution semantics.
    )

    @model_validator(mode="before")
    @classmethod
    def _wrap_audited_fields(cls, data: Any) -> Any:
        """Coerce raw env-var strings into placeholder :class:`AuditedSecret`s.

        pydantic-settings resolves env-vars as raw strings; the field's
        ``AuditedSecret`` type annotation (with
        ``arbitrary_types_allowed=True``) drives an ``is_instance_of``
        validator that rejects strings. This pre-validator runs FIRST and
        wraps every raw string for an :func:`audited_secret_field`-declared
        field into an :class:`AuditedSecret` with ``emit=None`` (the
        placeholder is rewrapped with the real ``emit`` + ``actor`` by
        :py:meth:`from_env` post-construction).

        Idempotent: if a field is already an :class:`AuditedSecret`,
        leave it alone.
        """
        if not isinstance(data, dict):
            return data
        coerced: dict[str, Any] = dict(data)
        for field_name, field_info in cls.model_fields.items():
            extra = field_info.json_schema_extra
            if not isinstance(extra, dict):
                continue
            secret_name = extra.get("audited_secret_name")
            if not isinstance(secret_name, str):
                continue
            # Look up the value by every key the user might have used:
            # the field name itself, plus the validation alias (if any —
            # which pydantic-settings uses as the env-var-derived key).
            candidate_keys = [field_name]
            alias = field_info.validation_alias
            if isinstance(alias, str):
                candidate_keys.append(alias)
            for key in candidate_keys:
                if key not in coerced:
                    continue
                value = coerced[key]
                if isinstance(value, AuditedSecret):
                    break
                if isinstance(value, str):
                    coerced[key] = AuditedSecret(
                        value,
                        secret_name=secret_name,
                        emit=None,
                        actor=_PLACEHOLDER_ACTOR,
                    )
                    break
        return coerced

    @classmethod
    def from_env(
        cls,
        *,
        emit: EmitCallable | None,
        actor: Actor,
        clock: Clock | None = None,
    ) -> Self:
        """Construct from env-vars + wrap secrets with the given audit callbacks.

        pydantic-settings resolves env-vars and validates the raw string
        values during ``cls()``; we then walk the field set and rewrap
        each :func:`audited_secret_field`-declared field's value into an
        :class:`AuditedSecret`.

        Args:
            emit:  Async event-log writer callable (or ``None`` to disable
                   emission for this settings instance — useful in tests
                   or sync-only operator scripts).
            actor: Identity recorded on every ``secret.accessed`` event
                   emitted by any wrapped field of this instance.
            clock: Optional :class:`Clock` for deterministic envelope
                   IDs + timestamps. Defaults to :class:`SystemClock`.

        Returns:
            A populated instance with each audited field carrying an
            :class:`AuditedSecret` wrapper.
        """
        instance = cls()  # subclass fields populated from env by pydantic-settings
        for field_name, field_info in cls.model_fields.items():
            extra = field_info.json_schema_extra
            if not isinstance(extra, dict):
                continue
            secret_name = extra.get("audited_secret_name")
            if not isinstance(secret_name, str):
                continue
            raw_value = getattr(instance, field_name)
            if isinstance(raw_value, AuditedSecret):
                # Already wrapped (idempotent re-call). Replace with a
                # fresh wrapper carrying the new emit/actor/clock —
                # callers reasonably expect from_env to override any
                # prior wrapping.
                raw_value = raw_value._value  # noqa: SLF001 — internal access by design
            wrapped = AuditedSecret(
                str(raw_value),
                secret_name=secret_name,
                emit=emit,
                actor=actor,
                clock=clock,
            )
            object.__setattr__(instance, field_name, wrapped)
        return instance

    def __repr__(self) -> str:
        # Defense-in-depth: even though every audited field's repr already
        # redacts, override the BaseSettings repr to format each field
        # via repr() (which calls AuditedSecret.__repr__) so accidental
        # repr(settings) emission cannot leak via a stray __dict__ access.
        cls_name = type(self).__name__
        parts = []
        for field_name in type(self).model_fields:
            value = getattr(self, field_name, None)
            parts.append(f"{field_name}={value!r}")
        return f"{cls_name}({', '.join(parts)})"

    __str__ = __repr__
