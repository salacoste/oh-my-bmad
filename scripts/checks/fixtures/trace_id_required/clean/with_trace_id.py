# Fixture: EventEnvelope.create(...) calls that DO pass trace_id= — CLEAN.
#
# The trace_id-bearing calls below must produce ZERO findings. To also exercise
# the suppression path (self-test requires each clean file to contain >=1
# missing-trace_id node), one missing-trace_id call is included but silenced via
# # noqa: TRACE001 <reason>.
from __future__ import annotations


class EventEnvelope:
    @classmethod
    def create(cls, **kwargs: object) -> EventEnvelope:
        return cls()


def emit_ok(trace_id: str) -> EventEnvelope:
    # Explicit trace_id= keyword: allowed.
    return EventEnvelope.create(type="x", trace_id=trace_id)


def emit_via_splat(params: dict[str, object]) -> EventEnvelope:
    # **kwargs splat may carry trace_id — fail-open, not flagged.
    return EventEnvelope.create(**params)


def emit_qualified(trace_id: str) -> object:
    import some_pkg  # type: ignore[import-not-found]  # noqa: F401

    # pkg.EventEnvelope.create(..., trace_id=...) attribute-chain form: allowed.
    return some_pkg.EventEnvelope.create(type="x", trace_id=trace_id)


def emit_suppressed() -> EventEnvelope:
    # Genuinely missing trace_id, but explicitly suppressed for the fixture.
    return EventEnvelope.create(type="x")  # noqa: TRACE001 — fixture: suppression path
