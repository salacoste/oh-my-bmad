"""Failure-detection emission primitives + in-memory tracking helpers (Story 2.10).

This module ships **callable primitives** for the four FR24a failure signals:

  - ``service.crashed``           — :func:`emit_service_crashed`
  - ``session.heartbeat_timeout`` — :func:`emit_session_heartbeat_timeout`
  - ``sink.delivery_failed``      — :func:`emit_sink_delivery_failed`
  - ``task.stop_requested``       — :func:`emit_task_stop_requested`

…plus two in-memory detection helpers used by the polling-loop wiring that
will be added in later epics:

  - :class:`HeartbeatMonitor`   — per-session last-heartbeat tracking with a
                                   ``> 2 × interval`` overdue boundary.
  - :class:`SinkFailureTracker` — per-sink consecutive-failure counter with a
                                   ``>= threshold`` emit gate.

**What this module does NOT do**

* No materializer handlers for any of the 4 new event types — state
  transitions (e.g. ``task.stop_requested`` → ``tasks.status = "stopped"``)
  are explicitly deferred (per AC-5) to Epic 3 (stop dispatch) and Epic 5
  (worker / session lifecycle). For ``task.stop_requested`` the materializer
  *does* extract the ``task_id`` and populates the ``events.task_id`` FK
  column; no handler-driven state mutation is performed in 2.10.
* No background polling loop — the **NFR-R5 60 s detection-to-emission
  SLA** is the contract of the polling-loop wiring (Epic 3 / Epic 5),
  not of this module. This module ships only the synchronous + async
  primitives that those loops will call.
* No process supervision, no Telegram-sink retry counter, no session
  table updates. Those are wired in their respective epics; Story 2.10
  ships the typed-event substrate so those wirings can reference known,
  schema-validated event types.

**Single-writer compliance**: every emission function appends through an
``EventLogWriter`` only — no SQLAlchemy mutation. ``check_single_writer``
remains green for ``services/registry-state``.

**Caller responsibility for ``last_error`` (defense-in-depth applied)**:
:func:`emit_sink_delivery_failed` writes a ``last_error`` string into the
event payload. The caller MUST sanitize it; in addition this module applies
:func:`_redact_last_error` as a belt-and-braces pass that masks common
secret-shaped tokens (Telegram bot tokens, ``Bearer ...`` headers,
``password=``/``token=``/``api_key=`` patterns) before the value reaches
the canonical event log. This does NOT relax the caller's contract — the
``scan-secrets`` gate runs over committed source — but it bounds the
blast radius of a missed sanitization at the emit boundary.

**Concurrency contract (Story 2.10 review-pass)**

Tracker state mutations and the ``should_emit`` / ``overdue_sessions``
predicates are all synchronous and contain no internal ``await``s.
Concurrent callers from a single asyncio loop see consistent state
between any pair of synchronous calls; the only window where two callers
could both decide "I should emit" for the same key is the gap between
``should_emit(key)`` and the await on ``writer.append``. To eliminate
that window, callers SHOULD prefer the combined ``*_and_mark`` helpers
(:meth:`SinkFailureTracker.should_emit_and_mark`,
:meth:`HeartbeatMonitor.overdue_sessions_and_mark`) which atomically
check + mark in a single statement.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from events import Actor, EventEnvelope
from events.clock import Clock
from events.envelope import ActorKind
from events.ids import new_event_id, new_request_id

from registry_state.adapters.event_log import EventLogWriter
from registry_state.domain.event_types import (
    ServiceCrashedPayload,
    SessionHeartbeatTimeoutPayload,
    SinkDeliveryFailedPayload,
    TaskStopRequestedPayload,
)

log = logging.getLogger(__name__)


# Story 9.8 D6 (Epic 9 retro): label used by failure-detection emits when the
# trace_id was minted synthetically because no operator context is available.
# Threaded through ``EventEnvelope.create(extensions=...)``; the materializer
# lifts it to ``events.trace_id_synthetic_source``. Callers that DO have an
# operator-originated trace_id MUST omit ``synthetic_source`` so the column
# stays NULL (real provenance).
_SYNTHETIC_SOURCE_LABEL_SYSTEM_INITIATED = "failure-detection-system-initiated"


def _synthetic_source_extensions(synthetic_source: str | None) -> dict[str, str] | None:
    """Return an ``extensions`` dict carrying the provenance label, or ``None``.

    Returns ``None`` (i.e. EventEnvelope.create defaults to ``{}``) when no
    label is requested — preserving the v1.0.1+ empty-extensions default
    for operator-originated traces.
    """
    if synthetic_source is None:
        return None
    return {"trace_id_synthetic_source": synthetic_source}


# ---------------------------------------------------------------------------
# Internal helpers
#
# Note on Actor.kind: the spec text in this story uses ``kind="service"`` but
# the canonical ``ActorKind`` Literal in ``packages/events/src/events/envelope.py``
# does not include ``"service"`` (allowed kinds: operator | orchestrator |
# worker | system | clawhip).  ``"system"`` is the correct default for
# system-initiated detection events (``service.crashed``,
# ``session.heartbeat_timeout``, ``sink.delivery_failed``).
# ``task.stop_requested`` accepts an ``actor_kind`` parameter so operator-
# initiated stops can use ``kind="operator"``.
# ---------------------------------------------------------------------------


def _assert_aware(dt: datetime, *, field: str) -> datetime:
    """Reject naive datetimes; return *dt* unchanged when tz-aware.

    Used by :class:`HeartbeatMonitor` to enforce that every stored timestamp
    carries a UTC offset, so cross-tz subtraction in :meth:`overdue_sessions`
    cannot raise ``TypeError`` at runtime under operational pressure.
    """
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise ValueError(
            f"{field} must be UTC-aware (tzinfo with zero offset); got tzinfo={dt.tzinfo!r}"
        )
    return dt


# Compiled once at import time. Order matters — telegram-token pattern is
# tried before generic key=value so the surrounding context survives the
# generic redactor's word-boundary chomp.
_TELEGRAM_TOKEN_RE: Final = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}")
_BEARER_TOKEN_RE: Final = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")
_KEYVALUE_SECRET_RE: Final = re.compile(r"(?i)\b(password|secret|token|api[_-]?key)\s*=\s*\S+")


def _redact_last_error(s: str | None) -> str | None:
    """Mask common secret patterns in a sink-delivery error string.

    Defense-in-depth at the emission boundary: callers SHOULD sanitize, but
    a missed token reaching the canonical event log is unrecoverable, so we
    apply a conservative regex-based redactor here. Patterns covered:

    * Telegram bot tokens (``<bot_id>:<token_body>`` shape).
    * ``Bearer <token>`` headers.
    * ``password=`` / ``secret=`` / ``token=`` / ``api_key=`` / ``api-key=``
      key/value pairs.

    Returns ``None`` unchanged. The redactor is a SAFETY NET — it is not a
    substitute for caller-side sanitization (the regex can never cover every
    secret shape).
    """
    if s is None:
        return None
    out = _TELEGRAM_TOKEN_RE.sub("<redacted-telegram-token>", s)
    out = _BEARER_TOKEN_RE.sub("Bearer <redacted>", out)
    out = _KEYVALUE_SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", out)
    return out


# ---------------------------------------------------------------------------
# Emission primitives (AC-3a)
#
# All four emit_* functions accept optional ``request_id`` and
# ``parent_event_id`` parameters (Story 2.10 review-pass) so callers can
# correlate multiple emissions from the same polling tick (request_id) and
# chain causality (parent_event_id) when the failure event is downstream of
# an explicit operator command or upstream detection event.
#
# ``Actor.id`` reflects the resolved ``actor_id`` *after* defaults are
# applied — for ``service.crashed`` the default is the value of the
# ``service`` parameter (the crashing process is the actor, not the
# detector). This is verified end-to-end by the AC-7 dual-write tests in
# ``test_failure_detection.py``.
#
# Story 9.7 pass-3 UH-7 TODO: these emit_* functions have ZERO production
# callers as of pass-3 (grep across services/+packages/ excluding tests).
# They are pre-emptive abstractions that pass-2 TH-B3 hardened (trace_id
# required) on a dead surface. The functions remain in place because
# downstream code may import them via ``__init__.py``; do NOT delete.
# Tracker: a future story should either (a) wire them into the polling
# loops they were originally designed for (Epic 3 / Epic 5 follow-up) or
# (b) fold their bodies into the call sites once those exist. See pass-3
# review notes (UH-7) for the audit trail.
# ---------------------------------------------------------------------------


async def emit_service_crashed(
    writer: EventLogWriter,
    *,
    clock: Clock,
    service: str,
    exit_code: int,
    trace_id: str,
    actor_id: str | None = None,
    request_id: str | None = None,
    parent_event_id: str | None = None,
    synthetic_source: str | None = None,
) -> EventEnvelope:
    """Emit a ``service.crashed`` event for a supervised process exit.

    The crashing service is the *actor* of this event; ``registry-state`` is
    only the detector. Therefore ``actor_id`` defaults to the ``service``
    parameter when not explicitly provided. Callers that want to record the
    detector identity instead can pass ``actor_id="registry-state"`` (or
    similar) explicitly.

    Args:
        writer:          Open :class:`EventLogWriter` (single-writer instance).
        clock:           Injected :class:`Clock` for ``emitted_at`` +
                         ``monotonic_ns``.
        service:         Logical service name, e.g. ``"worker-wrapper"``.
        exit_code:       Non-zero exit code reported by the supervisor
                         (the payload validator rejects ``0``).
        actor_id:        Override for ``Actor.id``. Defaults to ``service``
                         (the crashing process is the actor).
        trace_id:        REQUIRED bare UUIDv7 (Story 9.7 pass-2 TH-B3 /
                         pass-3 UH-7). System-initiated detectors MUST mint
                         a synthetic trace explicitly at the call site —
                         the previous "when None, synthetic minted" behaviour
                         was removed because it hid the cost of synthetic
                         traces and produced orphan chains undetectable at
                         review time.
        request_id:      Optional bare-UUIDv7 request id for correlation.
                         When ``None``, a fresh ``new_request_id(clock=...)``
                         is synthesized — failure events are internally
                         triggered, so they get their own request id.
        parent_event_id: Optional ``e-<uuidv7>`` to chain causality (e.g.
                         to a polling-tick parent event).

    Returns:
        The persisted :class:`EventEnvelope`.
    """
    # Story 9.7 pass-2 TH-B3: ``trace_id`` is REQUIRED — no silent mint.
    # System-initiated detectors (where there is no operator trace context)
    # MUST explicitly mint with ``new_request_id(clock=clock)`` and a
    # ``# system-initiated synthetic`` comment so the cost of synthetic
    # traces is visible at every call site.
    payload = ServiceCrashedPayload(service=service, exit_code=exit_code)
    resolved_actor_id = actor_id if actor_id is not None else service
    envelope = EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        type="service.crashed",
        schema_version="1.1.0",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind="system", id=resolved_actor_id),
        payload=payload,
        trace_id=trace_id,
        request_id=request_id if request_id is not None else new_request_id(clock=clock),
        parent_event_id=parent_event_id,
        # Story 9.8 D6: if the caller minted a synthetic trace_id, record
        # the provenance so the materializer can populate the
        # events.trace_id_synthetic_source column.
        extensions=_synthetic_source_extensions(synthetic_source),
    )
    await writer.append(envelope)
    return envelope


async def emit_session_heartbeat_timeout(
    writer: EventLogWriter,
    *,
    clock: Clock,
    session_id: str,
    task_id: str,
    last_heartbeat_at: datetime,
    timeout_threshold_s: float,
    trace_id: str,
    actor_id: str = "registry-state",
    request_id: str | None = None,
    parent_event_id: str | None = None,
    synthetic_source: str | None = None,
) -> EventEnvelope:
    """Emit a ``session.heartbeat_timeout`` event for an overdue session.

    Args:
        writer:              Open :class:`EventLogWriter`.
        clock:               Injected :class:`Clock`.
        session_id:          ``s-<uuidv7>`` of the overdue session.
        task_id:             ``t-<uuidv7>`` of the owning task.
        last_heartbeat_at:   UTC-aware timestamp of the session's last
                             heartbeat (naive datetimes are rejected by the
                             payload validator).
        timeout_threshold_s: Configured ``2 × heartbeat_interval_s`` boundary
                             (must be ``> 0`` and finite).
        actor_id:            Free-form actor identifier (the detector — the
                             default is ``"registry-state"`` because the
                             timeout signal is system-initiated).
        trace_id:            See :func:`emit_service_crashed`.
        request_id:          See :func:`emit_service_crashed`.
        parent_event_id:     See :func:`emit_service_crashed`.
    """
    # Story 9.7 pass-2 TH-B3: ``trace_id`` is REQUIRED (see emit_service_crashed).
    payload = SessionHeartbeatTimeoutPayload(
        session_id=session_id,
        task_id=task_id,
        last_heartbeat_at=last_heartbeat_at,
        timeout_threshold_s=timeout_threshold_s,
    )
    envelope = EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        type="session.heartbeat_timeout",
        schema_version="1.1.0",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind="system", id=actor_id),
        payload=payload,
        trace_id=trace_id,
        request_id=request_id if request_id is not None else new_request_id(clock=clock),
        parent_event_id=parent_event_id,
        # Story 9.8 D6: see emit_service_crashed.
        extensions=_synthetic_source_extensions(synthetic_source),
    )
    await writer.append(envelope)
    return envelope


async def emit_sink_delivery_failed(
    writer: EventLogWriter,
    *,
    clock: Clock,
    sink_name: str,
    consecutive_failures: int,
    trace_id: str,
    last_error: str | None = None,
    actor_id: str = "registry-state",
    request_id: str | None = None,
    parent_event_id: str | None = None,
    synthetic_source: str | None = None,
) -> EventEnvelope:
    """Emit a ``sink.delivery_failed`` event after ``consecutive_failures``.

    The caller SHOULD sanitize ``last_error``. As defense-in-depth, this
    function ALSO runs :func:`_redact_last_error` over the value before
    constructing the payload, masking common secret patterns (Telegram bot
    tokens, ``Bearer ...`` headers, ``password=``/``secret=``/``token=``/
    ``api_key=`` pairs). The redactor is conservative — it cannot cover
    every secret shape — but it bounds the blast radius of a missed
    sanitization.

    Args:
        writer:               Open :class:`EventLogWriter`.
        clock:                Injected :class:`Clock`.
        sink_name:            Logical sink identifier, e.g. ``"telegram"``.
        consecutive_failures: Current streak count (always ``>= threshold``
                              when the ``SinkFailureTracker.should_emit``
                              gate fires; payload validator enforces ``>= 1``).
        last_error:           Sanitized error description, or ``None`` (will
                              be passed through ``_redact_last_error``).
        actor_id:             Free-form detector identifier (default
                              ``"registry-state"``).
        trace_id:             See :func:`emit_service_crashed`.
        request_id:           See :func:`emit_service_crashed`.
        parent_event_id:      See :func:`emit_service_crashed`.

    Note:
        All ``emit_*`` functions accept fully kw-only arguments after
        ``writer``; the order shown here is for documentation only — call
        with whatever ordering is most readable at the call site.
    """
    # Story 9.7 pass-2 TH-B3: ``trace_id`` is REQUIRED (see emit_service_crashed).
    payload = SinkDeliveryFailedPayload(
        sink_name=sink_name,
        consecutive_failures=consecutive_failures,
        last_error=_redact_last_error(last_error),
    )
    envelope = EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        type="sink.delivery_failed",
        schema_version="1.1.0",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind="system", id=actor_id),
        payload=payload,
        trace_id=trace_id,
        request_id=request_id if request_id is not None else new_request_id(clock=clock),
        parent_event_id=parent_event_id,
        # Story 9.8 D6: see emit_service_crashed.
        extensions=_synthetic_source_extensions(synthetic_source),
    )
    await writer.append(envelope)
    return envelope


async def emit_task_stop_requested(
    writer: EventLogWriter,
    *,
    clock: Clock,
    task_id: str,
    actor_id: str,
    trace_id: str,
    actor_kind: ActorKind = "system",
    request_id: str | None = None,
    parent_event_id: str | None = None,
    synthetic_source: str | None = None,
) -> EventEnvelope:
    """Emit a ``task.stop_requested`` event for a stop request.

    ``actor_id`` is REQUIRED here (no default) — the entity that issued the
    stop must be identified, e.g. ``"telegram:12345678"`` for an operator
    or ``"registry-state"`` for a system-initiated stop.

    ``actor_kind`` defaults to ``"system"``; callers handling an operator
    /stop command (Telegram, console) MUST pass ``actor_kind="operator"``
    so the envelope reflects the true initiator.

    Args:
        writer:          Open :class:`EventLogWriter`.
        clock:           Injected :class:`Clock`.
        task_id:         ``t-<uuidv7>`` of the task being stopped.
        actor_id:        Identity of the actor that requested the stop.
        actor_kind:      ``"operator"`` for operator-initiated stops,
                         ``"system"`` (default) for internal triggers.
        trace_id:        See :func:`emit_service_crashed`.
        request_id:      See :func:`emit_service_crashed`.
        parent_event_id: See :func:`emit_service_crashed`. Particularly
                         useful here to chain to the operator command's
                         envelope.
    """
    # Story 9.7 pass-2 TH-B3: ``trace_id`` is REQUIRED — operator callers
    # MUST pass the operator's trace_id; system-initiated stops MUST mint
    # synthetic at the call site with ``# system-initiated synthetic`` so
    # the cost is visible.
    payload = TaskStopRequestedPayload(task_id=task_id, actor_id=actor_id)
    envelope = EventEnvelope.create(
        event_id=new_event_id(clock=clock),
        type="task.stop_requested",
        schema_version="1.1.0",
        emitted_at=clock.now(),
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind=actor_kind, id=actor_id),
        payload=payload,
        trace_id=trace_id,
        request_id=request_id if request_id is not None else new_request_id(clock=clock),
        parent_event_id=parent_event_id,
        # Story 9.8 D6: see emit_service_crashed.
        extensions=_synthetic_source_extensions(synthetic_source),
    )
    await writer.append(envelope)
    return envelope


# ---------------------------------------------------------------------------
# HeartbeatMonitor (AC-3b)
# ---------------------------------------------------------------------------


class HeartbeatMonitor:
    """In-memory tracker for per-session last-heartbeat timestamps.

    The overdue boundary uses **strict greater-than** ``2 × heartbeat_interval_s``
    — at exactly ``2×`` the session is NOT yet overdue. This matches the
    epic AC: "overdue by MORE THAN 2× its configured interval".

    **Edge-triggered overdue detection (review-pass)**: :meth:`overdue_sessions`
    returns sessions that are overdue *and have not yet been emit-marked*.
    Callers MUST call :meth:`mark_emitted` after a successful emission, OR
    use the combined :meth:`overdue_sessions_and_mark` helper which atomically
    checks + marks. Without this, a session that stays overdue across
    polling ticks would generate a fresh ``session.heartbeat_timeout``
    event on every tick.

    **Tz-awareness**: every stored timestamp must be UTC-aware
    (``_assert_aware`` enforces this in :meth:`record_heartbeat` for both
    ``clock.now()`` and externally-provided ``at`` values). This prevents
    runtime ``TypeError`` from tz-mixed subtraction in :meth:`overdue_sessions`.

    **Clock regression**: when an incoming heartbeat is older than the
    currently-stored value for the same session, the regression is logged
    and IGNORED (the newer timestamp is retained). This protects against
    out-of-order delivery from a system whose clock briefly went backward.

    Thread-safety: state is plain ``dict``s. All callers must run on a
    single asyncio loop (no cross-thread access). No locks.

    Performance: :meth:`overdue_sessions` is O(N) over all tracked
    sessions per call. For NFR-R5's polling-tick cadence (≥1s) this is
    fine. TODO Phase 2 if N grows large: switch to a sorted-by-timestamp
    structure.
    """

    def __init__(self, *, heartbeat_interval_s: float, clock: Clock) -> None:
        if not math.isfinite(heartbeat_interval_s):
            raise ValueError(f"heartbeat_interval_s must be finite; got {heartbeat_interval_s!r}")
        if heartbeat_interval_s <= 0:
            raise ValueError(f"heartbeat_interval_s must be positive; got {heartbeat_interval_s}")
        self._interval_s = heartbeat_interval_s
        self._clock = clock
        self._last_seen: dict[str, datetime] = {}
        # Sessions that have already been notified-on as overdue. Reset on
        # heartbeat refresh and on remove_session. Edge-triggers prevent
        # duplicate emission across consecutive polling ticks.
        self._emitted: set[str] = set()
        # Cache 2 × interval as a float once at construction time (E5).
        # Avoids repeated multiplication in the overdue hot-path and ensures
        # the ``timeout_threshold_s`` property and ``overdue_sessions`` always
        # agree on the threshold value.
        self._threshold_s: float = float(2 * heartbeat_interval_s)

    @property
    def timeout_threshold_s(self) -> float:
        """Convenience: the ``2 × interval`` value used in overdue checks.

        Returns the cached ``_threshold_s`` value (computed once at
        construction time) so the property and :meth:`overdue_sessions`
        always agree. Always a ``float`` so payload-side strict-mode
        validation never receives an ``int``.
        """
        return self._threshold_s

    def record_heartbeat(self, session_id: str, *, at: datetime | None = None) -> None:
        """Record a fresh heartbeat for *session_id*.

        Args:
            session_id: Logical session identifier (no shape enforcement
                here — the event payload validator checks shape at emission
                time).
            at: Optional UTC-aware timestamp. When ``None``, ``clock.now()``
                is used. When provided, must be tz-aware (raises
                ``ValueError`` otherwise). Useful for seeding state from
                event-log replay or for testing without a custom clock.

        Behavior on clock regression: if the new timestamp is *older* than
        the currently-stored timestamp for *session_id*, the new value is
        IGNORED and a warning is logged. The newer timestamp is retained.
        Refreshing also clears any prior emit-mark so the session can be
        reported overdue again after a future timeout.
        """
        if at is None:
            new_at = _assert_aware(self._clock.now(), field="clock.now()")
        else:
            new_at = _assert_aware(at, field="at")

        prior = self._last_seen.get(session_id)
        if prior is not None and new_at < prior:
            log.warning(
                "heartbeat regression ignored for session=%s (incoming=%s < stored=%s)",
                session_id,
                new_at.isoformat(),
                prior.isoformat(),
            )
            return

        self._last_seen[session_id] = new_at
        self._emitted.discard(session_id)
        log.debug(
            "heartbeat recorded session=%s at=%s",
            session_id,
            new_at.isoformat(),
        )

    def last_heartbeat_at(self, session_id: str) -> datetime | None:
        """Return the most-recently recorded heartbeat for *session_id*, or ``None``."""
        return self._last_seen.get(session_id)

    def remove_session(self, session_id: str) -> None:
        """Stop tracking *session_id*. No-op if not currently tracked."""
        self._last_seen.pop(session_id, None)
        self._emitted.discard(session_id)

    def __len__(self) -> int:
        """Return the number of currently-tracked sessions."""
        return len(self._last_seen)

    def tracked_sessions(self) -> list[str]:
        """Return a snapshot list of currently-tracked session IDs.

        Order matches insertion order (Python ≥3.7 dict guarantee; we
        require ≥3.12).
        """
        return list(self._last_seen)

    def mark_emitted(self, session_id: str) -> None:
        """Record that *session_id* has been notified-on as overdue.

        Subsequent calls to :meth:`overdue_sessions` will NOT include
        *session_id* until either :meth:`record_heartbeat` (refresh) or
        :meth:`remove_session` (untrack) clears the mark.
        """
        self._emitted.add(session_id)

    def overdue_sessions(self) -> list[tuple[str, datetime]]:
        """Return ``[(session_id, last_heartbeat_at)]`` for newly-overdue sessions.

        A session is overdue iff
        ``(now - last_heartbeat_at).total_seconds() > 2 * heartbeat_interval_s``.
        At exactly ``2×`` the session is NOT yet overdue (strict ``>``).
        Already-emit-marked sessions are EXCLUDED from the result —
        callers must call :meth:`mark_emitted` (or use
        :meth:`overdue_sessions_and_mark`) after a successful emission to
        prevent re-notification on the next polling tick.

        Iteration order matches insertion order (guaranteed by Python ≥3.7;
        we require ≥3.12).
        """
        now = _assert_aware(self._clock.now(), field="clock.now()")
        threshold = self._threshold_s
        result: list[tuple[str, datetime]] = []
        for session_id, last_at in self._last_seen.items():
            if session_id in self._emitted:
                continue
            elapsed = (now - last_at).total_seconds()
            if elapsed > threshold:
                log.info(
                    "session %s overdue (last=%s, elapsed=%.3fs, threshold=%.1fs)",
                    session_id,
                    last_at.isoformat(),
                    elapsed,
                    threshold,
                )
                result.append((session_id, last_at))
        return result

    def overdue_sessions_and_mark(self) -> list[tuple[str, datetime]]:
        """Atomically return newly-overdue sessions AND mark them as emitted.

        Equivalent to ``overdue_sessions()`` followed by ``mark_emitted(...)``
        for every returned session, but performed in a single synchronous
        call so concurrent callers from the same asyncio loop cannot both
        see the same session as "newly overdue".
        """
        result = self.overdue_sessions()
        for session_id, _ in result:
            self._emitted.add(session_id)
        return result

    def __repr__(self) -> str:
        return (
            f"HeartbeatMonitor(sessions={len(self._last_seen)}, "
            f"interval_s={self._interval_s}, "
            f"emitted={len(self._emitted)})"
        )


# ---------------------------------------------------------------------------
# SinkFailureTracker (AC-3c)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SinkFailureState:
    """Immutable snapshot of a sink's tracker state.

    Returned by :meth:`SinkFailureTracker.get_state`. Frozen dataclass for
    safe sharing across coroutines without defensive copies.
    """

    count: int
    last_error: str | None


# Per-sink count saturation. Prevents unbounded payload growth if a sink
# stays broken for days/weeks of polling ticks. 9999 is well above any
# realistic threshold (default 3) yet small enough that the canonical event
# log's payload size stays bounded.
_COUNT_CAP: Final[int] = 9999


class SinkFailureTracker:
    """Per-sink consecutive-failure counter with an emit-gate threshold.

    ``should_emit(sink_name)`` returns True when the current consecutive
    failure count is ``>= failure_threshold`` (default 3) AND the count has
    advanced since the last emit-mark for that sink (edge-triggered;
    review-pass).  ``record_success`` resets the counter to 0 (an
    interleaved success ends the streak) — but **preserves** the prior
    ``last_error`` so operators can see "what was the failure cause when
    the streak ended" via :meth:`get_state`.

    Counter saturation: ``record_failure`` caps the per-sink count at
    ``9999`` to prevent unbounded payload growth across long outages.

    **Edge-triggered emission (review-pass)**: callers MUST call
    :meth:`mark_emitted` after a successful emission, OR use the combined
    :meth:`should_emit_and_mark` helper which atomically checks + marks.
    Without this, a sink that stays broken for many polling ticks would
    generate a fresh ``sink.delivery_failed`` event on every tick.
    """

    def __init__(self, *, failure_threshold: int = 3) -> None:
        if failure_threshold < 1:
            raise ValueError(f"failure_threshold must be >= 1; got {failure_threshold}")
        self._threshold = failure_threshold
        self._state: dict[str, SinkFailureState] = {}
        # For each sink, the count value at which we last emitted; missing
        # = never emitted yet. Edge-triggers ``should_emit`` so a streak
        # that stays past threshold across ticks does not re-emit.
        self._emitted_at_count: dict[str, int] = {}

    @property
    def failure_threshold(self) -> int:
        """The configured emit-gate threshold."""
        return self._threshold

    def record_failure(self, sink_name: str, error: str | None = None) -> int:
        """Increment *sink_name*'s counter; return the new consecutive count.

        ``error`` (when provided) is stored as the most-recent ``last_error``
        for that sink and surfaced via :meth:`get_state`. Passing ``None``
        preserves the prior most-recent error. The count is capped at 9999.
        """
        prior = self._state.get(sink_name, SinkFailureState(count=0, last_error=None))
        new_count = min(prior.count + 1, _COUNT_CAP)
        # Preserve the prior last_error if this call passed None — callers
        # may pass error=None to count a failure whose error string is not
        # available without overwriting the most recent known error.
        new_error = error if error is not None else prior.last_error
        self._state[sink_name] = SinkFailureState(count=new_count, last_error=new_error)
        log.debug(
            "sink failure recorded sink=%s count=%d last_error=%s",
            sink_name,
            new_count,
            "<set>" if new_error is not None else "<none>",
        )
        return new_count

    def record_success(self, sink_name: str) -> None:
        """Reset *sink_name*'s consecutive-failure counter to 0.

        **Preserves** the prior ``last_error`` so operators can inspect
        what caused the failures when the streak ended. Also clears any
        ``mark_emitted`` checkpoint so a fresh streak past threshold can
        emit again. To wipe ``last_error`` entirely, follow with
        ``record_failure(sink_name, error=None)`` — but typically the
        operational signal is more useful retained.
        """
        prior = self._state.get(sink_name)
        last_error = prior.last_error if prior is not None else None
        self._state[sink_name] = SinkFailureState(count=0, last_error=last_error)
        self._emitted_at_count.pop(sink_name, None)
        log.debug("sink success recorded sink=%s (count reset to 0)", sink_name)

    def should_emit(self, sink_name: str) -> bool:
        """Return True when *sink_name*'s count is at threshold AND has advanced.

        Edge-triggered: returns True only when
        ``current_count >= failure_threshold`` AND
        ``current_count > emitted_at_count`` (or no prior emit-mark exists).
        Use :meth:`mark_emitted` (or :meth:`should_emit_and_mark`) to record
        the checkpoint after a successful emission.
        """
        prior = self._state.get(sink_name)
        if prior is None:
            return False
        if prior.count < self._threshold:
            return False
        emitted_at = self._emitted_at_count.get(sink_name, -1)
        return prior.count > emitted_at

    def mark_emitted(self, sink_name: str) -> None:
        """Record the count at which *sink_name* was last emit-notified.

        Subsequent :meth:`should_emit` calls return False until the count
        advances past this checkpoint OR :meth:`record_success` clears it.
        """
        prior = self._state.get(sink_name)
        if prior is None:
            return
        self._emitted_at_count[sink_name] = prior.count

    def should_emit_and_mark(self, sink_name: str) -> bool:
        """Atomically check :meth:`should_emit` AND :meth:`mark_emitted`.

        Returns True when the gate fires; in that case the emit-mark is
        also installed before the function returns. Use this instead of
        the two-call sequence whenever the polling loop has any chance of
        concurrent invocation on the same sink.
        """
        if not self.should_emit(sink_name):
            return False
        self.mark_emitted(sink_name)
        return True

    def remove_sink(self, sink_name: str) -> None:
        """Stop tracking *sink_name*. No-op if not currently tracked.

        Clears both the failure-state entry and the emit-mark checkpoint
        so that if the sink is re-added later its streak starts fresh.
        """
        self._state.pop(sink_name, None)
        self._emitted_at_count.pop(sink_name, None)

    def get_state(self, sink_name: str) -> SinkFailureState | None:
        """Return ``SinkFailureState(count, last_error)`` for *sink_name*, or ``None``.

        Returns ``None`` when *sink_name* has never been recorded (neither
        a failure nor a success). Use :meth:`is_tracked` for an explicit
        membership check.
        """
        return self._state.get(sink_name)

    def is_tracked(self, sink_name: str) -> bool:
        """Return ``True`` if *sink_name* has at least one recorded event."""
        return sink_name in self._state

    def __len__(self) -> int:
        """Return the number of currently-tracked sinks."""
        return len(self._state)

    def tracked_sinks(self) -> list[str]:
        """Return a snapshot list of currently-tracked sink names.

        Order matches insertion order (Python ≥3.7 dict guarantee; we
        require ≥3.12).
        """
        return list(self._state)

    def __repr__(self) -> str:
        return f"SinkFailureTracker(sinks={len(self._state)}, threshold={self._threshold})"


__all__ = [
    "HeartbeatMonitor",
    "SinkFailureState",
    "SinkFailureTracker",
    "SYNTHETIC_SOURCE_SYSTEM_INITIATED",
    "emit_service_crashed",
    "emit_session_heartbeat_timeout",
    "emit_sink_delivery_failed",
    "emit_task_stop_requested",
]


# Public alias for the system-initiated synthetic-source label so callers can
# reference the constant rather than repeating the literal at every emit site.
SYNTHETIC_SOURCE_SYSTEM_INITIATED: Final = _SYNTHETIC_SOURCE_LABEL_SYSTEM_INITIATED
