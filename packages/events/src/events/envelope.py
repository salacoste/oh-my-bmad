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
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from events.errors import EventSchemaUnknown
from events.schema_registry import EVENT_TYPES, REGISTRY

# UUIDv7 shape: `e-` prefix optional, 36-char hex with version-7 nibble.
_UUIDV7_CORE = r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_EVENT_ID_RE = re.compile(rf"^e-{_UUIDV7_CORE}$")
_UUIDV7_BARE_RE = re.compile(rf"^{_UUIDV7_CORE}$")
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")

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
        arbitrary_types_allowed=True,
    )

    event_id: str
    schema_version: str
    type: str
    emitted_at: datetime
    emitted_at_monotonic_ns: int = Field(ge=0)
    actor: Actor
    payload: dict[str, Any] | BaseModel
    parent_event_id: str | None = None
    trace_id: str | None = None
    request_id: str

    @field_validator("event_id")
    @classmethod
    def _event_id_shape(cls, v: str) -> str:
        if not _EVENT_ID_RE.match(v):
            raise ValueError(f"event_id must match ^e-<uuidv7>$ (got {v!r})")
        return v

    @field_validator("parent_event_id")
    @classmethod
    def _parent_event_id_shape(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _EVENT_ID_RE.match(v):
            raise ValueError(f"parent_event_id must match ^e-<uuidv7>$ (got {v!r})")
        return v

    @field_validator("request_id")
    @classmethod
    def _request_id_shape(cls, v: str) -> str:
        if not _UUIDV7_BARE_RE.match(v):
            raise ValueError(f"request_id must be a bare UUIDv7 (got {v!r})")
        return v

    @field_validator("schema_version")
    @classmethod
    def _schema_version_shape(cls, v: str) -> str:
        if not _SEMVER_RE.match(v):
            raise ValueError(f"schema_version must be semver MAJOR.MINOR.PATCH (got {v!r})")
        return v

    @field_validator("type")
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
        parent_event_id: str | None = None,
        trace_id: str | None = None,
        request_id: str,
    ) -> EventEnvelope:
        """Factory that enforces schema-registry membership.

        Raises ``EventSchemaUnknown`` if (type, schema_version) isn't
        registered. The payload dict is validated against the registered
        payload model when the caller passes a plain dict.
        """
        key = (type, schema_version)
        if key not in REGISTRY:
            raise EventSchemaUnknown(type, schema_version, EVENT_TYPES)
        model_cls = REGISTRY[key]
        if isinstance(payload, dict):
            validated_payload: dict[str, Any] | BaseModel = model_cls.model_validate(payload)
        else:
            validated_payload = payload
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
        )


# Re-export UTC for convenience in tests
__all__ = ["Actor", "ActorKind", "EventEnvelope", "UTC"]
