# Fixture: EventEnvelope.create(...) calls that OMIT trace_id= — VIOLATIONS (TRACE001).
#
# Exercises the visit_Call path for both the bare-name (EventEnvelope.create)
# and the attribute-chain (pkg.EventEnvelope.create) forms. All UNsuppressed.
from __future__ import annotations


class EventEnvelope:
    @classmethod
    def create(cls, **kwargs: object) -> EventEnvelope:
        return cls()


def emit_missing() -> EventEnvelope:
    # No trace_id= keyword anywhere → TRACE001.
    return EventEnvelope.create(type="x", request_id="r")


def emit_missing_qualified() -> object:
    import some_pkg  # type: ignore[import-not-found]  # noqa: F401

    # Attribute-chain form, still missing trace_id → TRACE001.
    return some_pkg.EventEnvelope.create(type="x")
