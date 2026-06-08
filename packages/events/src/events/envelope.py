"""Immutable event envelope — the single shared shape every platform event carries.

Architecture §line-401 mandate: no other event-envelope shape may be constructed;
all event emission flows through this model. `ConfigDict(frozen=True, strict=True)`
rejects post-construction mutation AND type coercion.

Field regex validators enforce the naming + shape conventions from Architecture
§Format Patterns + §Naming Patterns. Real UUIDv7 generation lands in Story 2.2
(``events.ids``); Story 2.1 validates *shape only* via regex so test envelopes
can construct with hard-coded UUIDv7-shaped literals until the generator lands.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from events import schema_registry
from events.errors import EventSchemaUnknown


class _FrozenDict(dict[str, Any]):
    """Immutable ``dict`` subclass — ``dict`` for Pydantic, frozen for operators.

    We subclass ``dict`` (rather than using ``MappingProxyType``) so Pydantic's
    strict-mode schema accepts it under the declared ``dict[str, Any]`` type
    without serializer warnings. All mutation methods are overridden to raise
    ``TypeError`` — attempting ``env.payload["k"] = v`` or
    ``env.payload.update(...)`` fails fast.
    """

    __slots__ = ()

    def __setitem__(self, key: str, value: Any) -> NoReturn:
        raise TypeError("EventEnvelope.payload is frozen; dict mutation rejected")

    def __delitem__(self, key: str) -> NoReturn:
        raise TypeError("EventEnvelope.payload is frozen; dict deletion rejected")

    def update(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("EventEnvelope.payload is frozen; dict.update rejected")

    def pop(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("EventEnvelope.payload is frozen; dict.pop rejected")

    def popitem(self) -> NoReturn:
        raise TypeError("EventEnvelope.payload is frozen; dict.popitem rejected")

    def clear(self) -> NoReturn:
        raise TypeError("EventEnvelope.payload is frozen; dict.clear rejected")

    def setdefault(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("EventEnvelope.payload is frozen; dict.setdefault rejected")


def _assert_json_safe(value: Any, *, path: str) -> None:
    """Recursively assert *value* is JSON-serializable.

    Rejects ``datetime``, ``set``, ``bytes``, ``NaN``/``Infinity``, ``UUID``,
    and any other non-JSON leaf types.

    Story 2.14 code-review fix M7. Pydantic accepts ``dict[str, Any]`` for any
    Python object, but the canonical-JSON encoder later crashes on non-JSON
    values. Reject at construction time so the error fires at the caller.

    Allowed leaf types: ``str``, ``bool``, ``int``, ``float`` (rejecting
    ``NaN``/``Inf``), ``None``. Allowed containers: ``dict[str, ...]``,
    ``list[...]`` (and ``tuple[...]`` — which canonical-JSON treats as list).
    Anything else raises :class:`ValueError` with the field path.
    """
    import math

    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        # Note: ``bool`` is a subclass of ``int`` — handled above.
        return
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"{path}: NaN/Infinity is not JSON-safe ({value!r})")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"{path}: dict keys must be str, got {type(k).__name__}")
            _assert_json_safe(v, path=f"{path}.{k}")
        return
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _assert_json_safe(item, path=f"{path}[{i}]")
        return
    raise ValueError(
        f"{path}: value of type {type(value).__name__!r} is not JSON-safe "
        f"(allowed: str/int/float/bool/None/dict/list)"
    )


def _deep_freeze_mapping(d: Mapping[str, Any]) -> _FrozenDict:
    """Return a ``_FrozenDict`` view with nested dicts/lists recursively frozen.

    Pydantic's ``frozen=True`` blocks attribute rebind (``env.payload = ...``)
    but does NOT block nested in-place mutation (``env.payload["k"] = v``)
    because the dict is held by reference. Deep-freeze at validation time so
    the frozen guarantee extends to payload contents.
    """
    frozen_inner: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, Mapping):
            frozen_inner[k] = _deep_freeze_mapping(v)
        elif isinstance(v, list):
            frozen_inner[k] = tuple(
                _deep_freeze_mapping(x) if isinstance(x, Mapping) else x for x in v
            )
        else:
            frozen_inner[k] = v
    # Construct via dict.__init__ path to bypass our overridden __setitem__.
    result = _FrozenDict.__new__(_FrozenDict)
    dict.__init__(result, frozen_inner)
    return result


# UUIDv7 shape: `e-` prefix optional, 36-char hex with version-7 nibble.
# Story 9.1 code-review F1: use `\Z` (not `$`) so trailing newline characters
# don't sneak past. Python's `re.match` only anchors start; `$` matches before
# a trailing `\n`. Switching to `\Z` closes the bypass across ALL envelope
# regex validators.
_UUIDV7_CORE = r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_EVENT_ID_RE = re.compile(rf"\Ae-{_UUIDV7_CORE}\Z")
_UUIDV7_BARE_RE = re.compile(rf"\A{_UUIDV7_CORE}\Z")
_SEMVER_RE = re.compile(r"\A[0-9]+\.[0-9]+\.[0-9]+\Z")
_EVENT_TYPE_RE = re.compile(r"\A[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+\Z")
# Story 9.1: trace_id accepts UUIDv7 (bare) OR Telegram-derived `tg:<update_id>`.
# Telegram update_id is a positive signed 64-bit int (1 ≤ update_id ≤ 9_223_372_036_854_775_807).
# Code-review F2: reject `tg:0` and leading-zero forms (`tg:01`, `tg:007`) to
# avoid aliasing where two distinct strings refer to the same update_id.
# Code-review F3: the int64 ceiling is enforced post-match (the 19-digit cap
# alone allows up to ~9.99e18 > 9.22e18 = int64 max).
_TRACE_ID_TELEGRAM_RE = re.compile(r"\Atg:(?P<update_id>[1-9][0-9]{0,18})\Z")
_INT64_MAX = 9_223_372_036_854_775_807

# Story 9.2 pass-1 review A1: public aliases + helper so downstream HTTP /
# Telegram / MCP / worker ingress middleware can validate inbound trace_id
# headers symmetrically with the envelope-side ``_trace_id_shape`` validator.
# Keeping a single source of truth here avoids the registry-api / telegram-
# gateway drift hazard called out by the Epic 9 retro.
TRACE_ID_TELEGRAM_RE = _TRACE_ID_TELEGRAM_RE
INT64_MAX_UPDATE_ID = _INT64_MAX


def is_valid_trace_id(value: str) -> bool:
    """Return True if *value* matches the Story 9.1 trace_id shape contract.

    Public counterpart to the envelope-side ``_trace_id_shape`` validator —
    use this from HTTP / Telegram / MCP / worker ingress middleware so the
    validation logic stays in one place (Epic 9 retro candidate).

    Accepted shapes:
      - bare UUIDv7 (e.g. ``01917e5c-a7d1-7000-8abc-...``)
      - Telegram-derived form ``tg:<update_id>`` where update_id is a positive
        decimal integer in ``[1, 2**63 − 1]`` with no leading zeros.

    Story 9.2 pass-2 review N13: defensive ``isinstance(value, str)`` guard
    so future downstream callers (Stories 9.3-9.6 — MCP / worker / console)
    that may pass user-decoded ``bytes`` or numeric values get ``False``
    instead of a ``TypeError`` from ``_UUIDV7_BARE_RE.match()``. This matches
    the ``parse_prefix`` defensive non-str gate in ``events.ids``.
    """
    if not isinstance(value, str):
        return False
    if _UUIDV7_BARE_RE.match(value):
        return True
    m = _TRACE_ID_TELEGRAM_RE.match(value)
    if m is not None:
        return int(m["update_id"]) <= _INT64_MAX
    return False


ActorKind = Literal["operator", "orchestrator", "worker", "system", "clawhip"]


class Actor(BaseModel):
    """Who emitted this event."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    kind: ActorKind
    id: str = Field(..., min_length=1)


class EventEnvelope(BaseModel):
    """The canonical event envelope.

    Constructed via ``EventEnvelope.create()`` to enforce registry membership,
    OR directly via ``EventEnvelope(...)`` / ``model_validate_json()`` when
    replaying from the event log (no registry round-trip required — the event
    has already been recorded).
    """

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        populate_by_name=True,
    )

    event_id: str
    schema_version: str = Field(default="1.1.0")
    type: str
    emitted_at: datetime
    emitted_at_monotonic_ns: int = Field(ge=0)
    actor: Actor
    payload: dict[str, Any] | BaseModel
    parent_event_id: str | None = None
    trace_id: str = Field(
        ...,
        description=(
            "Required since Story 9.7 / schema_version 1.1.0 — bumped from "
            "Optional. Story 9.1 shape contract: bare UUIDv7 OR 'tg:<update_id>'."
        ),
    )
    request_id: str
    extensions: dict[str, Any] = Field(default_factory=dict)
    """Reserved for forward-compatible per-event metadata (e.g., ``trace_id``
    when distributed tracing lands in Phase 2). Schema-version 1.0.1+; ignored
    by 1.0.0 consumers per NFR-M3 additive-only rule. v1.0.0 envelopes that
    omit the field receive the default ``{}``; v1.0.1 envelopes round-trip
    the explicit value unchanged. The model retains
    ``ConfigDict(extra="forbid")`` — ``extensions`` is now an EXPECTED field,
    not a silently ignored extra (Story 2.14 / FR22 / NFR-M3).

    Story 2.14 code-review fix M1: ``extensions`` is deep-frozen by the
    ``_freeze_extensions_dict`` validator below. Pydantic's
    ``frozen=True`` blocks attribute rebind (``env.extensions = ...``)
    but NOT nested mutation (``env.extensions["k"] = v``); the validator
    wraps the dict in :class:`_FrozenDict` so all mutation paths raise
    ``TypeError``.

    Code-review fix M7: the type annotation is intentionally
    ``dict[str, Any]`` — JSON-serializability is enforced by the
    ``_validate_extensions_json_safe`` validator (which rejects
    ``datetime``, ``set``, ``bytes``, ``NaN``, etc. at construction
    time). A stricter recursive ``JsonValue`` Pydantic alias was
    considered but Pydantic v2's recursive-type schema generation
    materially slows envelope construction; the model_validator path
    is both faster and gives clearer error messages."""

    @field_validator("event_id", mode="after")
    @classmethod
    def _event_id_shape(cls, v: str) -> str:
        if not _EVENT_ID_RE.match(v):
            raise ValueError(f"event_id must match ^e-<uuidv7>$ (got {v!r})")
        return v

    @field_validator("parent_event_id", mode="after")
    @classmethod
    def _parent_event_id_shape(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _EVENT_ID_RE.match(v):
            raise ValueError(f"parent_event_id must match ^e-<uuidv7>$ (got {v!r})")
        return v

    @field_validator("request_id", mode="after")
    @classmethod
    def _request_id_shape(cls, v: str) -> str:
        if not _UUIDV7_BARE_RE.match(v):
            raise ValueError(f"request_id must be a bare UUIDv7 (got {v!r})")
        return v

    @field_validator("trace_id", mode="after")
    @classmethod
    def _trace_id_shape(cls, v: str) -> str:
        # Story 9.1 (FR57 / Epic 9 foundation): accept UUIDv7 OR Telegram-derived
        # form. Stories 9.2/9.4/9.5/9.6 emit UUIDv7; Story 9.3 emits `tg:<update_id>`.
        # Story 9.7: trace_id is now REQUIRED; the previous ``v is None`` branch
        # is dead because Pydantic rejects ``None`` against ``str`` before this
        # validator runs.
        if _UUIDV7_BARE_RE.match(v):
            return v
        m = _TRACE_ID_TELEGRAM_RE.match(v)
        if m is not None:
            # F3 carry-over: regex caps at 19 digits but ~9.99e18 > int64 max.
            # The regex guarantees ASCII digits so int() cannot raise.
            # Pass-2 review: error message reworded — "out of range" describes
            # the symptom to a library consumer; the int64 specifics live in
            # the implementation comment, not the user-facing text.
            # Story 9.2 pass-1 review B7: named group ``update_id`` instead of
            # slice ``v[3:]`` — slightly more defensive against future prefix
            # changes.
            if int(m["update_id"]) > _INT64_MAX:
                raise ValueError(
                    f"trace_id Telegram update_id out of range (max {_INT64_MAX}); got {v!r}"
                )
            return v
        raise ValueError(
            f"trace_id must be a bare UUIDv7 OR match \\Atg:<update_id>\\Z (got {v!r})"
        )

    @field_validator("schema_version", mode="after")
    @classmethod
    def _schema_version_shape(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise ValueError(f"schema_version must be semver MAJOR.MINOR.PATCH (got {v!r})")
        return v

    @field_validator("type", mode="after")
    @classmethod
    def _type_shape(cls, v: str) -> str:
        if not _EVENT_TYPE_RE.match(v):
            raise ValueError(f"type must be dotted lowercase past-tense (got {v!r})")
        return v

    @field_validator("emitted_at")
    @classmethod
    def _emitted_at_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("emitted_at must be timezone-aware (UTC)")
        if v.utcoffset() != timedelta(0):
            raise ValueError(f"emitted_at must be UTC (got offset {v.utcoffset()})")
        # Canonicalize at parse time: normalize to millisecond precision +
        # canonical ``tzinfo=UTC`` regardless of input format (``Z`` vs
        # ``+00:00`` vs zero-offset-named-"GMT"; microsecond vs millisecond
        # precision). This makes round-trip serialization byte-stable — the
        # validated datetime is the fixpoint of the canonical-JSON encoder.
        return datetime(
            v.year,
            v.month,
            v.day,
            v.hour,
            v.minute,
            v.second,
            (v.microsecond // 1000) * 1000,
            tzinfo=UTC,
        )

    @field_validator("payload", mode="after")
    @classmethod
    def _freeze_payload_dict(cls, v: dict[str, Any] | BaseModel) -> dict[str, Any] | BaseModel:
        """Deep-freeze dict payloads to a ``_FrozenDict`` (dict subclass).

        Pydantic's ``frozen=True`` blocks attribute rebind (``env.payload = ...``)
        but does NOT block nested mutation (``env.payload["k"] = v``). This
        validator deep-wraps dict payloads in ``_FrozenDict`` so the frozen
        guarantee extends to payload contents. BaseModel payloads get this
        same guarantee from their own ``frozen=True`` config (future payload
        models MUST declare ``ConfigDict(frozen=True)``).
        """
        if isinstance(v, dict) and not isinstance(v, _FrozenDict):
            return _deep_freeze_mapping(v)
        return v

    @field_serializer("payload")
    @classmethod
    def _serialize_payload(cls, v: dict[str, Any] | BaseModel) -> dict[str, Any]:
        """Serialize BaseModel payloads to dict for JSON output.

        Pydantic >=2.12 serializes ``dict[str, Any] | BaseModel`` unions
        incorrectly when the value is a BaseModel — the ``dict[str, Any]``
        branch produces ``{}`` instead of the model's fields. Explicit
        serialization bypasses the union dispatch.
        """
        if isinstance(v, BaseModel):
            return v.model_dump(mode="python")
        # dict(v) intentionally strips _FrozenDict wrapping — serialization
        # output should be a plain mutable dict, not a frozen view.
        return dict(v)

    @field_validator("extensions", mode="after")
    @classmethod
    def _freeze_and_validate_extensions(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Validate JSON-safety + deep-freeze the ``extensions`` dict.

        Story 2.14 code-review fixes M1 + M7:

        * **M1** — ``frozen=True`` blocks attribute rebind but not nested
          mutation. Wrap the dict in :class:`_FrozenDict` and recursively
          freeze nested dicts/lists. Mirrors ``_freeze_payload_dict``.
        * **M7** — ``dict[str, Any]`` would otherwise silently accept
          non-JSON values (``datetime``, ``set``, ``bytes``, ``NaN``,
          ``Infinity``, ``UUID``, etc.) which crash later at canonical-JSON
          serialization. Validate JSON-safety eagerly so the
          construct-time error fires at the offending caller, not at the
          serializer half a stack-frame away.
        """
        _assert_json_safe(v, path="extensions")
        if not isinstance(v, _FrozenDict):
            return _deep_freeze_mapping(v)
        return v

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        schema_version: str,
        type: str,
        emitted_at: datetime,
        emitted_at_monotonic_ns: int,
        actor: Actor | dict[str, Any],
        payload: dict[str, Any] | BaseModel,
        trace_id: str,
        parent_event_id: str | None = None,
        request_id: str,
        extensions: dict[str, Any] | None = None,
    ) -> EventEnvelope:
        """Factory that enforces schema-registry membership.

        Raises ``EventSchemaUnknown`` if (type, schema_version) isn't
        registered. The payload dict is validated against the registered
        payload model when the caller passes a plain dict.

        ``extensions`` defaults to ``{}`` (Story 2.14 / NFR-M3). Callers on
        v1.0.0 omit the kwarg; callers on v1.0.1+ pass the forward-compatible
        per-event metadata dict.

        Story 9.7: ``trace_id`` is now a REQUIRED kwarg (no default, no
        fallback). The transitional ``DeprecationWarning`` from Story 9.1 has
        been removed; missing ``trace_id`` raises ``pydantic.ValidationError``.
        Callers obtain a value via :func:`events.envelope.is_valid_trace_id`
        validated upstream input, or by minting a fresh UUIDv7 at the entry
        boundary.
        """
        key = (type, schema_version)
        if key not in schema_registry.REGISTRY:
            raise EventSchemaUnknown(type, schema_version, schema_registry.EVENT_TYPES)
        model_cls = schema_registry.REGISTRY[key]
        if isinstance(payload, dict):
            validated_payload: dict[str, Any] | BaseModel = model_cls.model_validate(payload)
        elif isinstance(payload, model_cls):
            validated_payload = payload
        else:
            # Payload is a BaseModel but wrong subclass — round-trip via dict
            # to enforce the registered payload-model contract (Fix F).
            validated_payload = model_cls.model_validate(payload.model_dump())
        actor_obj = actor if isinstance(actor, Actor) else Actor.model_validate(actor)
        return cls(
            event_id=event_id,
            schema_version=schema_version,
            type=type,
            emitted_at=emitted_at,
            emitted_at_monotonic_ns=emitted_at_monotonic_ns,
            actor=actor_obj,
            payload=validated_payload,
            parent_event_id=parent_event_id,
            trace_id=trace_id,
            request_id=request_id,
            extensions=extensions if extensions is not None else {},
        )


# Re-export UTC for convenience in tests
__all__ = [
    "Actor",
    "ActorKind",
    "EventEnvelope",
    "INT64_MAX_UPDATE_ID",
    "TRACE_ID_TELEGRAM_RE",
    "UTC",
    "is_valid_trace_id",
]
