"""AuditedSecret + AuditedBaseSettings — secret access audit emission (Story 2.16).

This module ships the **infrastructure** for FR42 / NFR-S3:

* :class:`AuditedSecret` wraps a single secret value and emits a typed
  ``secret.accessed`` event on every read of the ``.value`` property.
  The wrapper redacts under ``repr()`` / ``str()`` so accidental logging
  prints ``<REDACTED:secret_name>`` rather than the plaintext. It also
  participates in Pydantic serialization (``model_dump`` /
  ``model_dump_json``) via a custom core schema that emits the redacted
  string instead of walking ``__dict__`` / ``__slots__``.

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
  module-level ``stdlib`` logger, emission silently skipped, value
  returned unchanged. Acceptable: sync-only contexts (e.g.,
  config-validation scripts) don't have access to a running
  :class:`EventLogWriter` anyway.
* **Closed running loop**: if ``loop.create_task`` raises
  ``RuntimeError("Event loop is closed")``, a WARNING is logged and the
  read still returns the value (the ``.value`` "always returns" invariant
  cannot be broken by an audit-side failure).
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

Subclass ``model_validator(mode="after")`` limitation
-----------------------------------------------------

``AuditedBaseSettings.from_env`` rewraps each audited field via
``object.__setattr__`` *after* the standard ``cls()`` validation runs.
Subclass ``model_validator(mode='after')`` validators see the
*placeholder*-wrapped fields (``emit=None``, sentinel actor) — they do
NOT re-run after the rewrap with the real ``(emit, actor)``. If a
subclass needs cross-field invariants over the secret VALUES, validate
explicitly inside ``from_env`` after rewrap (or call
``cls.model_validate(instance.__dict__)`` manually).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import weakref
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, ClassVar

import structlog
from events.clock import Clock, SystemClock
from events.envelope import Actor, EventEnvelope
from events.errors import EventSchemaUnknown
from events.ids import new_event_id, new_request_id
from pydantic import AliasChoices, Field, model_validator
from pydantic import ValidationError as _PydanticValidationError
from pydantic.fields import FieldInfo
from pydantic_core import CoreSchema, core_schema
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from typing import Self

__all__ = [
    "AuditedBaseSettings",
    "AuditedSecret",
    "audited_secret_field",
    "flush_pending_emissions",
]

# Type alias for the (async) emission callable. The wrapper accepts ``None``
# to disable emission entirely (sync-only test/CLI contexts).
EmitCallable = Callable[[EventEnvelope], Awaitable[None]]

# Stdlib logger. Tests assert log-record presence via ``caplog``; structlog
# proxies to stdlib only when configured to do so, but stdlib ``logging``
# is always wired into pytest's caplog fixture by default. We use stdlib
# directly for the operational paths (no-loop skip, closed-loop skip,
# emission failure, unconfigured-actor warning) so tests can rely on
# log-record capture without needing structlog→stdlib bridge config.
_stdlib_logger = logging.getLogger("secret_hygiene.audited_secret")

# Structlog logger retained for richer structured logging in production
# code paths where stdlib semantics are insufficient. Currently unused
# directly; reserved for future structured-log additions.
_struct_log = structlog.get_logger("secret_hygiene.audited_secret")

# Charset constraint for ``secret_name``: ASCII alphanumerics plus ``.``,
# ``_``, ``-``. 1..128 chars. Defense-in-depth — matches the registry-state
# ``SecretAccessedPayload`` validator semantics (1..128) and additionally
# rejects whitespace / control / non-ASCII (which the registry-state
# ``min_length=1, max_length=128`` constraint does not enforce on its own).
_SECRET_NAME_PATTERN = re.compile(r"\A[A-Za-z0-9._-]{1,128}\Z")


def _validate_secret_name(secret_name: str, *, source: str) -> None:
    """Raise ``ValueError`` if *secret_name* fails the canonical regex.

    Args:
        secret_name: Candidate secret identifier to validate.
        source:      Human label for the error context (e.g.,
                     ``"AuditedSecret"`` or ``"audited_secret_field"``).
    """
    if not isinstance(secret_name, str) or not _SECRET_NAME_PATTERN.fullmatch(secret_name):
        raise ValueError(
            f"{source}.secret_name must match [A-Za-z0-9._-]{{1,128}} (got {secret_name!r})"
        )


# Sentinel actor stamped onto pre-validator placeholder wrappers. Detected
# by identity in :py:meth:`AuditedSecret.value` so reads from a bare
# ``MySettings(...)`` (without ``from_env``) emit a loud warning rather
# than silently producing an incomplete audit trail (review-fix H5).
# Defined HERE (before :class:`AuditedSecret`) because the class
# references it directly.
_UNCONFIGURED_ACTOR: Actor = Actor(kind="system", id="UNCONFIGURED-call-from_env")


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

    Pydantic integration: ``AuditedSecret`` provides a
    ``__get_pydantic_core_schema__`` so that any pydantic model
    serialization (``model_dump`` / ``model_dump_json``) emits the
    redacted ``"<REDACTED:secret_name>"`` form rather than walking the
    instance and leaking ``_value``.

    Args:
        value:        The plaintext secret. Stored on ``self._value`` and
                      returned by ``.value``. NEVER serialized into the
                      emitted event payload.
        secret_name:  Stable identifier used by the payload + repr.
                      Must satisfy ``[A-Za-z0-9._-]{1,128}``.
        emit:         Async callable that persists an :class:`EventEnvelope`
                      (typically ``EventLogWriter.append``). Pass ``None``
                      to disable emission entirely.
        actor:        Identity recorded on every emitted envelope. Reused
                      across all reads of this secret instance.
        clock:        Optional :class:`Clock` for deterministic IDs +
                      timestamps. Defaults to :class:`SystemClock`.
    """

    __slots__ = (
        "_actor",
        "_clock",
        "_emit",
        "_secret_name",
        "_unconfigured_warning_emitted",
        "_value",
    )

    def __init__(
        self,
        value: str,
        *,
        secret_name: str,
        emit: EmitCallable | None,
        actor: Actor,
        clock: Clock | None = None,
    ) -> None:
        _validate_secret_name(secret_name, source="AuditedSecret")
        self._value = value
        self._secret_name = secret_name
        self._emit = emit
        self._actor = actor
        self._clock = clock if clock is not None else SystemClock()
        self._unconfigured_warning_emitted = False

    @property
    def value(self) -> str:
        """Read the secret. Best-effort emission of ``secret.accessed``.

        Behavior matrix (see module docstring for full rationale):

        * ``emit=None``     → no emission, no warning, return value.
        * Running loop      → schedule emission as fire-and-forget task,
                              return value immediately.
        * No running loop   → log WARNING, skip emission, return value.
        * Closed loop       → log WARNING, skip emission, return value.

        Emission failures are logged but **never** propagate; the secret
        read always succeeds.

        H5: a wrapper carrying :data:`_UNCONFIGURED_ACTOR` (constructed by
        :py:meth:`AuditedBaseSettings._wrap_audited_fields` and never
        rewrapped via ``from_env``) emits a loud WARNING on the FIRST
        read so the operator notices that audit is silently disabled.
        """
        if self._actor is _UNCONFIGURED_ACTOR and not self._unconfigured_warning_emitted:
            _stdlib_logger.warning(
                "AuditedSecret was constructed via bare BaseSettings() "
                "without from_env(...) — emission disabled, audit "
                "trail INCOMPLETE for secret %r",
                self._secret_name,
            )
            self._unconfigured_warning_emitted = True
        if self._emit is not None:
            self._schedule_emission()
        return self._value

    def unwrap_for_rewrap(self) -> str:
        """Internal use only — expose the plaintext value for rewrap.

        :py:meth:`AuditedBaseSettings.from_env` calls this to retrieve
        the raw value when rewrapping a placeholder ``AuditedSecret``
        (constructed by the pre-validator with ``emit=None`` + sentinel
        actor) into one carrying the real ``(emit, actor)``. Never use
        from regular code: regular reads MUST go through ``.value`` so
        the audit event fires.
        """
        return self._value

    def _schedule_emission(self) -> None:
        """Build envelope + schedule fire-and-forget emit on the running loop.

        Splits out so subclasses / tests can override scheduling without
        rewriting the value-read path.
        """
        # Re-entrant emission guard: an emit callable that itself reads
        # another AuditedSecret would otherwise schedule a new task per
        # read, with no fan-out bound. Skip nested emissions.
        if _in_emission.get():
            _stdlib_logger.debug(
                "secret.accessed emission skipped — re-entrant read "
                "inside another emission for secret %r",
                self._secret_name,
            )
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _stdlib_logger.warning(
                "secret.accessed emission skipped — no running event loop "
                "(secret_name=%r actor.kind=%s actor.id=%s)",
                self._secret_name,
                self._actor.kind,
                self._actor.id,
            )
            return

        envelope = self._build_envelope()
        if envelope is None:
            return  # construction failed; warning already logged in _build_envelope
        # Type narrowing: _schedule_emission is only called when emit is not None
        emit = self._emit
        assert emit is not None  # noqa: S101 — invariant of the call site
        try:
            task = loop.create_task(self._safe_emit(emit, envelope))
        except RuntimeError as exc:
            # "Event loop is closed" or similar — runner is shutting down.
            # Audit-side failures must NEVER break the .value contract.
            _stdlib_logger.warning(
                "secret.accessed emission skipped — loop.create_task "
                "rejected (closed loop?) for secret %r: %s",
                self._secret_name,
                exc,
            )
            return
        # Hold a reference on a module-level WeakSet so the task isn't
        # GC'd mid-flight (asyncio's create_task only weak-refs the
        # task). The done-callback discards the entry once the task
        # settles.
        _live_emission_tasks.add(task)
        task.add_done_callback(_on_emission_done)

    def _build_envelope(self) -> EventEnvelope | None:
        """Construct the canonical ``secret.accessed`` envelope.

        ``EventEnvelope.create()`` form is intentional — it routes through
        the schema registry (Story 2.1) and validates the payload against
        the :class:`SecretAccessedPayload` model registered in
        :mod:`registry_state.domain.event_types`. The
        ``check_event_registry`` gate scans for direct
        ``EventEnvelope(...)`` construction and ``<receiver>.emit(...)``
        patterns; ``EventEnvelope.create()`` is outside the scanner's
        scope (vacuously green per Story 2.10's pattern, AC-12).

        Story 9.7: trace_id is now REQUIRED on EventEnvelope.  Secret-access
        audit events are internally-generated (no operator request supplies a
        trace_id), so we mint a synthetic bare UUIDv7 here.  This gives each
        audit envelope its own unique correlation handle without fabricating an
        end-to-end trace (the correct design: audit != operator-initiated trace).

        Returns ``None`` on construction failure (e.g. schema-registry drift)
        so the caller can skip emission without crashing the secret read.
        """
        try:
            return EventEnvelope.create(
                event_id=new_event_id(clock=self._clock),
                schema_version="1.1.0",
                type="secret.accessed",
                emitted_at=self._clock.now(),
                emitted_at_monotonic_ns=self._clock.monotonic_ns(),
                actor=self._actor,
                payload={"secret_name": self._secret_name, "scope": "read"},
                trace_id=new_request_id(clock=self._clock),
                request_id=new_request_id(clock=self._clock),
            )
        except (EventSchemaUnknown, _PydanticValidationError) as exc:
            # PH-A6/E10: narrow exception — only schema-registry drift and
            # Pydantic validation failures are expected/recoverable here.
            # TypeError / AttributeError / AssertionError are programmer errors
            # and must propagate so CI catches them immediately.
            # Structured log "audit_emission_dropped" makes this alertable.
            _stdlib_logger.warning(
                "audit_emission_dropped secret_name=%r error_type=%s detail=%s "
                "— NFR-S3 audit event not emitted; alert if this fires in production",
                self._secret_name,
                type(exc).__name__,
                exc,
            )
            return None

    @staticmethod
    async def _safe_emit(emit: EmitCallable, envelope: EventEnvelope) -> None:
        """Run *emit* and swallow + log non-critical exceptions.

        Audit emission failures must never crash the caller. Log at
        ``error`` level so the failure is observable; the secret-read
        consumer already returned successfully by the time this runs.

        Critical control-flow exceptions —
        :class:`KeyboardInterrupt`, :class:`SystemExit`,
        :class:`asyncio.CancelledError` — propagate naturally
        (Python contract: never swallow these). The broad ``except
        Exception`` catches everything else.
        """
        token = _in_emission.set(True)
        try:
            try:
                await emit(envelope)
            except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
                raise
            except Exception as exc:  # noqa: BLE001 — audit must not propagate
                _stdlib_logger.error(
                    "secret.accessed emission failed (event_id=%s "
                    "secret_name=%s error_type=%s): %s",
                    envelope.event_id,
                    envelope.payload.get("secret_name")
                    if isinstance(envelope.payload, dict)
                    else getattr(envelope.payload, "secret_name", None),
                    type(exc).__name__,
                    exc,
                )
        finally:
            _in_emission.reset(token)

    def __repr__(self) -> str:
        return f"<REDACTED:{self._secret_name}>"

    __str__ = __repr__

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: Any,
        _handler: Any,
    ) -> CoreSchema:
        """Custom core schema for pydantic ``model_dump``/``model_dump_json``.

        Without this, pydantic would walk ``__slots__`` (or fall back to
        ``__dict__`` introspection) and serialize ``_value`` directly,
        leaking the plaintext secret. The custom schema:

        * On validation: accept either an existing ``AuditedSecret``
          instance (passthrough — covers ``model_validate`` round-trips
          on already-wrapped fields) OR a ``str`` (no-op error path —
          there's no way to construct a meaningful ``AuditedSecret``
          from a bare string without a ``secret_name``; this branch
          is reachable only if a caller bypasses
          :class:`AuditedBaseSettings`'s pre-validator, in which case
          the field will be a placeholder built elsewhere). Returning
          the instance unchanged is correct for the passthrough case.
        * On serialization: emit ``"<REDACTED:secret_name>"``.
        """

        def _ser(instance: AuditedSecret) -> str:
            return f"<REDACTED:{instance._secret_name}>"

        return core_schema.no_info_plain_validator_function(
            function=lambda v: v,  # accept already-wrapped instance as-is
            serialization=core_schema.plain_serializer_function_ser_schema(
                _ser,
                return_schema=core_schema.str_schema(),
                when_used="always",
            ),
        )


# Module-level WeakSet holding live emission tasks so they survive long
# enough to complete. asyncio's create_task only holds a weak reference;
# without this anchor a fire-and-forget task can be garbage-collected
# before its coroutine awaits the writer. ``WeakSet`` (rather than a
# regular ``set``) means tasks GC'd before completion (theoretical safety
# net for multi-loop processes) auto-clean. The done-callback in
# :py:meth:`AuditedSecret._schedule_emission` discards the entry once the
# task settles.
_live_emission_tasks: weakref.WeakSet[asyncio.Task[Any]] = weakref.WeakSet()


def _on_emission_done(task: asyncio.Task[Any]) -> None:
    """Done-callback for a fire-and-forget emission task.

    Defensively retrieves ``task.exception()`` to mark any captured
    exception as retrieved (Python's "exception was never retrieved"
    warning at interpreter shutdown is otherwise emitted for
    fire-and-forget tasks that raised). Then discards the task from
    the live-task anchor.
    """
    with contextlib.suppress(BaseException):
        task.exception()
    # WeakSet.discard is idempotent — safe even if the task was already GC'd.
    _live_emission_tasks.discard(task)


# Re-entrant emission guard. ``_safe_emit`` sets this True for the duration
# of an ``await emit(...)``; ``_schedule_emission`` checks and bails out
# if True so an emit callable that itself reads an :class:`AuditedSecret`
# does not produce unbounded task fan-out.
_in_emission: ContextVar[bool] = ContextVar(
    "secret_hygiene_audited_secret_in_emission", default=False
)


async def flush_pending_emissions(timeout: float = 1.0) -> None:
    """Wait for all in-flight emission tasks to complete (or *timeout*).

    Service shutdown should call this before tearing down the event-log
    writer so no fire-and-forget emissions are dropped::

        # in lifespan teardown:
        await flush_pending_emissions(timeout=5.0)

    If *timeout* expires before all tasks complete, a WARNING is logged
    listing the count still pending; the function returns normally
    (does not raise). Always returns immediately if no tasks are
    pending.
    """
    pending = list(_live_emission_tasks)
    if not pending:
        return
    _done, pending_after = await asyncio.wait(pending, timeout=timeout)
    if pending_after:
        _stdlib_logger.warning(
            "flush_pending_emissions: %d task(s) did not complete within %ss",
            len(pending_after),
            timeout,
        )


# ---------------------------------------------------------------------------
# Pydantic-settings integration
# ---------------------------------------------------------------------------


# Module-level sentinel marker stamped into the field's ``json_schema_extra``
# at declaration time. Identity comparison (``is``) is the canonical
# detection mechanism in :py:meth:`AuditedBaseSettings.from_env` — foreign
# code that happens to set ``json_schema_extra={"audited_secret_name":
# ...}`` without using :func:`audited_secret_field` will NOT carry this
# marker and thus will NOT be wrapped.
_AUDITED_FIELD_MARKER = object()


# Sentinel for ``audited_secret_field(default=...)`` to distinguish
# "no default provided" (required field) from ``default=None`` (literal
# None default — optional field). Cannot use ``None`` itself because
# Pydantic treats both ``None`` and "no default" identically when no
# explicit default-factory is set.
class _UnsetType:
    """Sentinel type for absent-default detection."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<UNSET>"


_UNSET: _UnsetType = _UnsetType()


def audited_secret_field(
    secret_name: str,
    *,
    env_var: str | None = None,
    default: str | None | _UnsetType = _UNSET,
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
    env-var name (otherwise pydantic-settings derives it from the field
    name uppercased). ``default`` distinguishes:

    * absent (default): required field; pydantic-settings raises if the
      env-var is unset and no validation_alias provides a value.
    * ``None``: literal ``None`` default — field is optional.
    * ``str``: the literal string default.

    Returns:
        A :class:`pydantic.fields.FieldInfo` whose metadata is consumed
        by :class:`AuditedBaseSettings`. Annotated as :data:`typing.Any`
        for the same reason :func:`pydantic.Field` is — so callers can
        write ``x: AuditedSecret = audited_secret_field(...)`` in a
        model body without mypy flagging an ``assignment`` mismatch
        between the declared type and the ``FieldInfo`` factory result.
    """
    _validate_secret_name(secret_name, source="audited_secret_field")
    extra: dict[str, Any] = {
        "audited_secret_name": secret_name,
        "_audit_marker": _AUDITED_FIELD_MARKER,
    }
    kwargs: dict[str, Any] = {
        "json_schema_extra": extra,
    }
    if env_var is not None:
        kwargs["validation_alias"] = env_var
    if isinstance(default, _UnsetType):
        # Required field — no default. pydantic-settings will raise on
        # missing env-var.
        return Field(**kwargs)
    # Literal None or str default.
    return Field(default=default, **kwargs)


# NOTE: ``_UNCONFIGURED_ACTOR`` is defined earlier in the module (right
# after ``_validate_secret_name``) so :py:meth:`AuditedSecret.value` can
# reference it. The block here is intentionally kept as a marker; the
# canonical definition is above the :class:`AuditedSecret` class.


def _is_audited_field(field_info: FieldInfo) -> tuple[bool, str | None]:
    """Return ``(is_audited, secret_name)`` for *field_info*.

    Detection uses the identity of :data:`_AUDITED_FIELD_MARKER` so
    foreign code carrying the dict key without the marker is not wrapped.
    """
    extra = field_info.json_schema_extra
    if not isinstance(extra, dict):
        return False, None
    if extra.get("_audit_marker") is not _AUDITED_FIELD_MARKER:
        return False, None
    secret_name = extra.get("audited_secret_name")
    if not isinstance(secret_name, str):
        return False, None
    return True, secret_name


def _candidate_keys_for_field(field_name: str, field_info: FieldInfo) -> list[str]:
    """Compute the env-var candidate keys for *field_info*.

    Handles plain string aliases AND :class:`AliasChoices` (iterating
    its string members). Non-string members (e.g., :class:`AliasPath`)
    are skipped — declaring a path-style alias on an audited secret is
    not a supported pattern.
    """
    keys = [field_name]
    alias = field_info.validation_alias
    if isinstance(alias, str):
        keys.append(alias)
    elif isinstance(alias, AliasChoices):
        for choice in alias.choices:
            if isinstance(choice, str):
                keys.append(choice)
    # Other alias types (AliasPath etc.) intentionally ignored.
    return keys


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

    ``arbitrary_types_allowed=True`` is set so the ``AuditedSecret``
    type annotation is accepted. ``BaseSettings`` defaults to
    ``extra="ignore"``; we keep that default so unrelated env vars
    don't break instantiation.

    Subclass uniqueness invariant: ``__init_subclass__`` rejects
    declarations where two audited fields share the same
    ``secret_name`` (the audit trail would otherwise be ambiguous).

    Example::

        class MySettings(AuditedBaseSettings):
            anthropic_api_key: AuditedSecret = audited_secret_field(
                "anthropic_api_key", env_var="ANTHROPIC_API_KEY"
            )

        settings = MySettings.from_env(emit=writer.append, actor=Actor(...))
        # ... later, in async context:
        key = settings.anthropic_api_key.value  # emits secret.accessed
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        arbitrary_types_allowed=True,
        # ``extra`` not specified — ``BaseSettings`` defaults to
        # ``"ignore"`` so unrelated env-vars won't break instantiation.
    )

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Validate audited-field declarations after Pydantic class init.

        Enforces secret_name uniqueness across all audited fields in
        the subclass. Two fields sharing the same ``secret_name`` would
        produce indistinguishable audit events — fail-fast at import.

        Uses ``__pydantic_init_subclass__`` (not stdlib
        ``__init_subclass__``) because Pydantic's metaclass populates
        ``model_fields`` *after* ``type.__new__`` calls
        ``__init_subclass__``; the pydantic-specific hook runs once
        the class is sufficiently initialized.
        """
        super().__pydantic_init_subclass__(**kwargs)
        seen: dict[str, str] = {}  # secret_name -> field_name
        for fname, finfo in cls.model_fields.items():
            is_audited, sname = _is_audited_field(finfo)
            if not is_audited or sname is None:
                continue
            if sname in seen:
                raise ValueError(
                    f"AuditedBaseSettings subclass {cls.__name__!r}: "
                    f"secret_name {sname!r} declared on both "
                    f"{seen[sname]!r} and {fname!r}; each audited field "
                    f"must have a unique secret_name."
                )
            seen[sname] = fname

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

        Conflict detection: if multiple candidate keys (field name + any
        validation_alias choices) are simultaneously present in *data*
        with DIFFERENT values, ``ValueError`` is raised — silent
        first-match would otherwise hide a configuration mistake.
        """
        if not isinstance(data, dict):
            return data
        coerced: dict[str, Any] = dict(data)
        for field_name, field_info in cls.model_fields.items():
            is_audited, secret_name = _is_audited_field(field_info)
            if not is_audited or secret_name is None:
                continue
            candidate_keys = _candidate_keys_for_field(field_name, field_info)
            present = [k for k in candidate_keys if k in coerced]
            if len(present) > 1:
                distinct = {coerced[k] for k in present}
                if len(distinct) > 1:
                    raise ValueError(
                        f"audited field {field_name!r}: conflicting "
                        f"values via {present}: {sorted(map(repr, distinct))}"
                    )
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
                        actor=_UNCONFIGURED_ACTOR,
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
        **overrides: Any,
    ) -> Self:
        """Construct from env-vars + wrap secrets with the given audit callbacks.

        pydantic-settings resolves env-vars and validates the raw string
        values during ``cls(**overrides)``; we then walk the field set
        and rewrap each :func:`audited_secret_field`-declared field's
        value into an :class:`AuditedSecret`.

        Caller-supplied ``**overrides`` are forwarded to ``cls(...)`` so
        settings can be partially constructed in tests without poking
        env-vars (e.g.,
        ``MySettings.from_env(emit=..., actor=..., other_field="x")``).

        Subclass ``model_validator(mode='after')`` validators run on the
        placeholder-wrapped instance during ``cls(**overrides)`` — they
        do NOT re-run after the rewrap with the real ``(emit, actor)``.
        See module docstring for the full caveat.

        Args:
            emit:      Async event-log writer callable (or ``None`` to
                       disable emission for this settings instance —
                       useful in tests or sync-only operator scripts).
            actor:     Identity recorded on every ``secret.accessed``
                       event emitted by any wrapped field of this
                       instance.
            clock:     Optional :class:`Clock` for deterministic envelope
                       IDs + timestamps. Defaults to :class:`SystemClock`.
            overrides: Additional kwargs passed through to
                       ``cls(**overrides)``.

        Returns:
            A populated instance with each audited field carrying an
            :class:`AuditedSecret` wrapper.
        """
        instance = cls(**overrides)  # subclass fields populated from env
        for field_name, field_info in cls.model_fields.items():
            is_audited, secret_name = _is_audited_field(field_info)
            if not is_audited or secret_name is None:
                continue
            raw_value = getattr(instance, field_name)
            if isinstance(raw_value, AuditedSecret):
                # Already wrapped (idempotent re-call). Replace with a
                # fresh wrapper carrying the new emit/actor/clock —
                # callers reasonably expect from_env to override any
                # prior wrapping.
                raw_str = raw_value.unwrap_for_rewrap()
            elif raw_value is None:
                raise ValueError(
                    f"audited secret field {field_name!r} resolved to "
                    f"None — set env-var or provide default="
                )
            else:
                raw_str = str(raw_value)
            if raw_str == "":
                raise ValueError(
                    f"audited secret field {field_name!r} resolved to "
                    f"empty string — set env-var or provide default="
                )
            wrapped = AuditedSecret(
                raw_str,
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
