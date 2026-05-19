"""Prometheus metric collectors for the β metrics-subscriber (Stories 10.3 + 10.4).

The `MetricsState` dataclass is the single mutable cell that the tail
loop (`metrics_subscriber.__main__.run_subscriber`) updates on every
envelope (Story 10.4) and on every ``maybe_persist`` cycle (Story 10.3).
The FastAPI ``/metrics`` route reads the same ``CollectorRegistry`` so
HTTP scrapes always observe the most recent gauge / counter values.

Design rationale (Story 10.2 P2-H4 + P3-H2 lesson — applied):
  - **Per-app** ``CollectorRegistry``, NOT ``prometheus_client.REGISTRY``
    global.  Test isolation: pytest sessions that exercise this module
    via ``build_app`` see a fresh registry per app instance, so cross-
    test metric value leak is impossible.  The architectural sketch at
    ``architecture.md:1207`` shows ``generate_latest()`` (the global
    form); we intentionally deviate, documented at the registry-
    construction call site.
  - **MetricsState as a plain dataclass holding `Gauge` / `Counter`
    instances**, NOT a subclass of ``prometheus_client.MetricsCore``.
    Reason: ``MetricsCore`` is the surface for *custom collectors*
    (e.g. exposing third-party metric sources); we don't need that —
    we need a place to hold and mutate built-in metric objects from
    the tail loop.  Dataclass is simpler and avoids the
    ``collect()`` -hook scaffold that custom collectors require.
  - **Cross-thread safety**: ``prometheus_client`` gauges and counters
    are thread-safe (each holds an internal ``threading.Lock``).  The
    ``CollectorRegistry`` itself is NOT safe for concurrent
    *registration* but is safe for concurrent ``.collect()``.  Per-app
    registry construction happens once in lifespan startup before any
    HTTP request handler is attached, so the unsafe window never
    overlaps with concurrent access.

Metric inventory (Stories 10.3 + 10.4 combined):

  +---------------------------------------------+---------+-------------+
  | Metric                                      | Type    | Labels      |
  +---------------------------------------------+---------+-------------+
  | ``metrics_subscriber_lag_seconds``          | Gauge   | (none)      |
  | ``metrics_subscriber_bytes_behind``         | Gauge   | (none)      |
  | ``metrics_subscriber_cursor_offset_bytes``  | Gauge   | path        |
  | ``metrics_subscriber_parse_skip_total``     | Counter | reason      |
  | ``omb_task_lifecycle_events_total``         | Counter | event_type  |
  | ``omb_session_lifecycle_events_total``      | Counter | phase       |
  | ``omb_secret_accessed_total``               | Counter | actor_kind  |
  | ``omb_events_appended_total``               | Counter | event_family|
  | ``omb_task_tokens_spent``                   | Gauge   | task_id     |
  | ``omb_idempotency_cache_total``             | Counter | outcome     |
  | ``omb_capability_denied_total``             | Counter | tier,bound  |
  +---------------------------------------------+---------+-------------+

Cardinality discipline (P2-I3, ADR-0005 §Cardinality):

  - All counter labels are bounded enums pre-populated at
    ``build_collectors`` time (Story 10.3 P1-H1 lesson — eliminates the
    lazy-registration race between ``Counter.labels(...).inc()`` from
    the tail-loop thread and ``generate_latest()`` from a concurrent
    scrape).  See module-level ``_TASK_LIFECYCLE_EVENT_TYPES`` etc.
  - ``omb_task_tokens_spent`` is the ONLY metric with an unbounded
    label (``task_id``); cardinality is bounded by the active-task
    count and the cleanup invariant: on ``task.completed`` /
    ``task.stop_requested`` the labelled gauge child is removed via
    ``gauge.remove(task_id)``.
  - Unknown envelope types increment ONLY
    ``omb_events_appended_total{event_family=<prefix>}`` where the
    prefix is itself an enum — no novel ``event_type`` leaks into a
    Counter label (Story 10.5 cardinality regression test enforces).
  - ``path`` (cursor offset) is bounded to today / yesterday (≤ 2 active
    values during day-rollover); ``reason`` is a finite enum.

Deferred-FROM-FR62 metrics (Story 10.4 D1):

  - ``omb_idempotency_cache_total`` (outcomes: ``cache_hit``,
    ``factory_ran``) — pre-populated with zero values.  Requires
    ``idempotency.cache_hit`` / ``idempotency.factory_ran`` events
    from ``registry-api``; cross-service emission deferred to Story
    10.4.x / 11.x.
  - ``omb_capability_denied_total`` (tier × boundary 6 combinations) —
    pre-populated with zero values.  Requires
    ``capability.denied{tier, boundary}`` events from
    ``TierEnforcementMiddleware``; cross-service emission deferred to
    Story 10.4.x / 11.x.

  Pre-registering the metric NAMES keeps operator dashboards stable
  during the deferral window.  See ADR-0005 §Deferred Metrics.

Actor-kind enum deviation (Story 10.4 spec-drift note):

  The story 10.4 spec AC3 enumerates ``actor_kind`` as
  ``{human, system, agent}`` (a logical bucketing).  The actual
  envelope's ``Actor.kind`` enum
  (:data:`events.envelope.ActorKind`) is
  ``{operator, orchestrator, worker, system, clawhip}``.  We use the
  ACTUAL enum values as the bounded label set — labelling by an
  imaginary three-bucket projection would either drop information or
  require a mapping function that doesn't exist anywhere else in the
  codebase.  Documented in the Story 10.4 Dev Agent Record.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from events import EventEnvelope
from prometheus_client import CollectorRegistry, Counter, Gauge

# ---------------------------------------------------------------------------
# Bounded-enum label sets (Story 10.4 — pre-populated at build_collectors).
#
# Cross-checked against ``services/registry-state/src/registry_state/domain/
# event_types.py`` ``register()`` calls (Story 10.3 P1-M1 lesson — spec
# enums MUST match actual registrations).  See module docstring for the
# actor_kind spec-drift note.
# ---------------------------------------------------------------------------

#: Bounded enum of task-lifecycle envelope ``type`` values that increment
#: ``omb_task_lifecycle_events_total``.  15 values per Story 10.4 AC1
#: (cross-checked against ``event_types.py:140-258``).
_TASK_LIFECYCLE_EVENT_TYPES: Final[tuple[str, ...]] = (
    "task.created",
    "task.planning.started",
    "task.plan.ready",
    "task.execution.started",
    "task.step.completed",
    "task.blocker_raised",
    "task.approval_requested",
    "task.completed",
    "task.stop_requested",
    "task.retry_requested",
    "task.self_recovered",
    "task.execution.resumed",
    "task.budget_exceeded",
    "task.license_flagged",
    "task.summary_emitted",
)

#: Bounded enum of session phases for ``omb_session_lifecycle_events_total``
#: (Story 10.4 AC2).  Derived from envelope ``type`` via the
#: :data:`_SESSION_TYPE_TO_PHASE` mapping below.
_SESSION_PHASES: Final[tuple[str, ...]] = (
    "started",
    "heartbeat",
    "finished",
    "heartbeat_timeout",
    "reconnecting",
)

#: Mapping from ``envelope.type`` to ``phase`` label for the session
#: counter — single source of truth (Story 10.4 AC2).
_SESSION_TYPE_TO_PHASE: Final[dict[str, str]] = {
    "session.started": "started",
    "session.heartbeat": "heartbeat",
    "session.finished": "finished",
    "session.heartbeat_timeout": "heartbeat_timeout",
    "session.reconnecting": "reconnecting",
}

#: Bounded enum of actor kinds.  Uses the ACTUAL
#: :data:`events.envelope.ActorKind` values (Story 10.4 spec-drift note
#: in module docstring).
_ACTOR_KINDS: Final[tuple[str, ...]] = (
    "operator",
    "orchestrator",
    "worker",
    "system",
    "clawhip",
)

#: Bounded enum of event families (envelope.type prefix before first dot).
#: 11 values per Story 10.4 AC4.  Currently registered families (10):
#: agent, approval, file, secret, service, session, sink, task, telegram,
#: tier3.  ``deployment`` is included for forward-compatibility per the
#: AC4 enum table (no registered events yet — counter sample stays at 0
#: until a future story adds the event family).
_EVENT_FAMILIES: Final[tuple[str, ...]] = (
    "task",
    "session",
    "approval",
    "secret",
    "tier3",
    "service",
    "sink",
    "agent",
    "file",
    "telegram",
    "deployment",
)

#: Bounded enum of idempotency-cache outcomes (Story 10.4 AC8 — DEFERRED).
_IDEMPOTENCY_OUTCOMES: Final[tuple[str, ...]] = (
    "cache_hit",
    "factory_ran",
)

#: Bounded enum of capability tiers (Story 10.4 AC8 — DEFERRED).
_CAPABILITY_TIERS: Final[tuple[str, ...]] = ("tier1", "tier2", "tier3")

#: Bounded enum of capability boundaries (Story 10.4 AC8 — DEFERRED).
_CAPABILITY_BOUNDARIES: Final[tuple[str, ...]] = ("mcp", "http")


@dataclass(slots=True)
class MetricsState:
    """In-process holder for the Story 10.3 + 10.4 metric collectors.

    One instance per ``FastAPI`` app — created in lifespan startup,
    stored on ``app.state.metrics``, mutated by the tail loop, read
    by the ``/metrics`` HTTP handler via the registry it shares with
    the app.

    Attributes (Story 10.3):
        registry: The per-app :class:`prometheus_client.CollectorRegistry`
            shared with the ``/metrics`` route.
        lag_seconds: Gauge — wall-clock lag of the most recently
            processed envelope vs ``datetime.now(UTC)``.
        bytes_behind: Gauge — bytes between today's JSONL file size
            and the subscriber's cursor offset at the most recent
            persist (snapshot, not a running average).
        cursor_offset_bytes: Gauge — current cursor offset, labelled
            by ``path`` for day-rollover introspection.
        parse_skip_total: Counter — lines skipped during JSONL tail,
            labelled by ``reason`` (bounded enum).

    Attributes (Story 10.4 — FR62 metric set):
        task_lifecycle_total: Counter — task lifecycle envelope counter,
            labelled by ``event_type`` (15-value bounded enum).
        session_lifecycle_total: Counter — session lifecycle envelope
            counter, labelled by ``phase`` (5-value bounded enum).
        secret_accessed_total: Counter — ``secret.accessed`` envelope
            counter, labelled by ``actor_kind`` (5-value bounded enum).
        events_appended_total: Counter — every-envelope counter for the
            FR62 1m/5m/1h append-rate computation via PromQL
            ``rate(...)``, labelled by ``event_family`` (11-value
            bounded enum).
        task_tokens_spent: Gauge — per-task token-spend snapshot.
            Labelled by ``task_id``; cleaned via ``.remove(task_id)``
            on task termination to bound cardinality.
        idempotency_cache_total: Counter — DEFERRED-preview per
            Story 10.4 D1; pre-populated at zero.
        capability_denied_total: Counter — DEFERRED-preview per
            Story 10.4 D1; pre-populated at zero.
    """

    registry: CollectorRegistry
    lag_seconds: Gauge
    bytes_behind: Gauge
    cursor_offset_bytes: Gauge
    parse_skip_total: Counter
    # Story 10.4 — FR62 core metric set.
    task_lifecycle_total: Counter
    session_lifecycle_total: Counter
    secret_accessed_total: Counter
    events_appended_total: Counter
    task_tokens_spent: Gauge
    # Story 10.4 AC8 — DEFERRED preview counters.
    idempotency_cache_total: Counter
    capability_denied_total: Counter

    def record_lag(self, *, lag_seconds: float, bytes_behind: int) -> None:
        """Update the two label-free persist-time gauges.

        Called by :func:`metrics_subscriber.run_subscriber` from
        within the tail-loop coroutine on each persist event.  Each
        underlying ``Gauge.set()`` is thread-safe via its own internal
        lock.

        Atomicity note (Story 10.3 pass-1 P1-H3): the two ``.set()``
        calls are NOT performed under a shared lock — at a Prometheus
        scrape boundary the two gauges may reflect different persist
        events for a microsecond-scale window.  Operationally this is
        fine for correlated alerting rules (the Prometheus scrape
        interval is 15s and the split-brain window is sub-microsecond,
        well within scrape jitter).  For strict atomicity a future
        revision could introduce a custom collector that takes a single
        lock and emits both samples from one ``collect()`` call; see
        ADR-0005 §atomicity-note for the trade-off.

        Negative ``lag_seconds`` (clock skew with writer running
        ahead) is intentionally not clamped — the metric reflects the
        observed datetime delta verbatim so dashboards can alert on
        out-of-sync clocks.  ``bytes_behind`` is always non-negative
        by construction in ``_emit_lag_log``.
        """
        self.lag_seconds.set(lag_seconds)
        self.bytes_behind.set(bytes_behind)

    def record_cursor(self, *, path: Path, offset: int) -> None:
        """Update the cursor-offset gauge for the labelled path.

        ``path`` is converted to a string and used directly as the
        label value; cardinality is bounded by the day-rollover policy
        (today + yesterday → ≤ 2 active labels at any given moment).
        """
        self.cursor_offset_bytes.labels(path=str(path)).set(offset)

    def on_parse_skip(self, reason: str) -> None:
        """Increment the parse-skip counter for the given reason.

        Bound as an ``on_skip`` callback into
        :func:`events.log_reader.iter_new_envelopes_since` (Story 10.3
        AC6 wiring).  ``reason`` is a bounded-enum string — see the
        cardinality discipline note in the module docstring.
        """
        self.parse_skip_total.labels(reason=reason).inc()


# ---------------------------------------------------------------------------
# Story 10.4 AC6 — event-type → metric-update dispatch helpers + table.
#
# All updaters are pure (state-mutation only; no I/O, no logging).  They
# MUST handle missing payload fields gracefully (envelope schema_version
# drift): use ``_payload_get`` not ``envelope.payload.token_usage``.  See
# :func:`update_for` for the registry-of-dispatchers contract.
# ---------------------------------------------------------------------------


EventMetricUpdater = Callable[[MetricsState, EventEnvelope], None]


def _payload_get(envelope: EventEnvelope, field: str) -> Any:
    """Tolerant payload-field accessor.

    ``EventEnvelope.payload`` is typed as ``dict[str, Any] | BaseModel``;
    log-replay round-trips it as a ``dict`` while ``EventEnvelope.create``
    promotes it to the registered Pydantic model.  We accept both so a
    dispatch updater works against either shape without an isinstance
    branch at every call site.

    Returns ``None`` for missing fields — never raises ``AttributeError``
    or ``KeyError``.  This is the canonical defensive pattern for the
    Story 10.4 dispatchers per AC6 ("handle missing payload fields
    gracefully").
    """
    payload = envelope.payload
    if isinstance(payload, dict):
        return payload.get(field)
    return getattr(payload, field, None)


def _update_task_lifecycle(state: MetricsState, envelope: EventEnvelope) -> None:
    """Increment ``omb_task_lifecycle_events_total{event_type=<type>}``."""
    state.task_lifecycle_total.labels(event_type=envelope.type).inc()


def _update_task_lifecycle_and_clear_task_gauge(
    state: MetricsState, envelope: EventEnvelope
) -> None:
    """Increment the lifecycle counter + remove the per-task token gauge.

    Wired for ``task.completed`` and ``task.stop_requested`` — the two
    envelope types that terminate a task.  The gauge cleanup is the
    load-bearing cardinality invariant (Story 10.4 AC5): without it the
    ``task_id`` label set would grow unbounded.

    Out-of-order resilience: if a ``task.completed`` envelope arrives
    BEFORE the final ``task.step.completed`` for the same task (clock
    skew or replay), the cleanup runs first then the late step re-
    creates a labelled child.  The steady-state observation is still
    correct: the metric will be cleaned on the next terminator.  See
    ``test_task_gauge_cleanup_then_resurrect_is_idempotent``.

    Atomicity note (Story 10.3 P1-H3 honest claim): the increment and
    the ``.remove()`` are TWO operations — between them a concurrent
    scrape can observe the incremented counter while the gauge child
    is still present.  This is fine: dashboards aggregate over time
    windows that dwarf the microsecond-scale window.
    """
    state.task_lifecycle_total.labels(event_type=envelope.type).inc()
    task_id = _payload_get(envelope, "task_id")
    if isinstance(task_id, str) and task_id:
        # ``Gauge.remove(...)`` raises ``KeyError`` if no labelled child
        # exists for this task — out-of-order ``task.completed`` before
        # any token-emitting envelope is benign and must not crash the
        # updater.
        with contextlib.suppress(KeyError):
            state.task_tokens_spent.remove(task_id)


def _update_task_tokens(state: MetricsState, envelope: EventEnvelope) -> None:
    """Set ``omb_task_tokens_spent{task_id=...}`` from envelope payload.

    Wired for ``task.execution.started``, ``task.step.completed``, and
    ``task.budget_exceeded`` — the envelopes that may carry a token-
    usage field.  Field-name tolerance: ``token_usage`` (the field on
    :class:`TaskCompletedPayload`) OR ``tokens_used`` (the field on
    :class:`TaskBudgetExceededPayload`) — whichever is present and
    non-None is used.  ``task.execution.started`` and
    ``task.step.completed`` currently do not carry token fields; the
    gauge update is a no-op for those envelopes.  This conservative
    "set what you see, do nothing otherwise" rule keeps the updater
    forward-compatible: when a future schema_version bumps add token
    fields to those payloads, the gauge starts tracking automatically.

    Atomicity note (Story 10.3 P1-H3 honest claim): the
    ``Gauge.set()`` IS a single atomic op via the internal lock.  The
    lifecycle counter increment + gauge set sequence (called via the
    dispatch table) is NOT atomic across the two metrics, but each
    individual metric mutation is.
    """
    # Increment the lifecycle counter first (event observed regardless
    # of token field presence).
    state.task_lifecycle_total.labels(event_type=envelope.type).inc()
    task_id = _payload_get(envelope, "task_id")
    if not (isinstance(task_id, str) and task_id):
        return
    tokens = _payload_get(envelope, "token_usage")
    if tokens is None:
        tokens = _payload_get(envelope, "tokens_used")
    if isinstance(tokens, int) and tokens >= 0:
        state.task_tokens_spent.labels(task_id=task_id).set(tokens)


def _update_session_lifecycle(state: MetricsState, envelope: EventEnvelope) -> None:
    """Increment ``omb_session_lifecycle_events_total{phase=<phase>}``."""
    phase = _SESSION_TYPE_TO_PHASE.get(envelope.type)
    if phase is None:
        # Unknown session.* envelope type — dispatch table guarantees
        # only the 5 registered types reach this updater, but the
        # defensive check keeps the function safe to call directly
        # from tests.
        return
    state.session_lifecycle_total.labels(phase=phase).inc()


def _update_secret_accessed(state: MetricsState, envelope: EventEnvelope) -> None:
    """Increment ``omb_secret_accessed_total{actor_kind=<kind>}``.

    Reads ``envelope.actor.kind`` directly (typed
    :class:`events.envelope.Actor`); the actor field is always populated
    (envelope schema invariant).
    """
    state.secret_accessed_total.labels(actor_kind=envelope.actor.kind).inc()


#: Story 10.4 AC6 — immutable event_type → updater dispatch table.
#:
#: Keys: 15 task lifecycle + 5 session + 1 secret = 21 entries.
#:
#: Lookup MUST use ``_DISPATCH.get(...)`` — direct subscript would
#: raise ``KeyError`` for legitimate unknown envelope types (those still
#: increment the events_appended counter via :func:`update_for`).
_DISPATCH: Final[dict[str, EventMetricUpdater]] = {
    # Task lifecycle — non-terminal events use the simple updater.
    "task.created": _update_task_lifecycle,
    "task.planning.started": _update_task_lifecycle,
    "task.plan.ready": _update_task_lifecycle,
    "task.blocker_raised": _update_task_lifecycle,
    "task.approval_requested": _update_task_lifecycle,
    "task.retry_requested": _update_task_lifecycle,
    "task.self_recovered": _update_task_lifecycle,
    "task.execution.resumed": _update_task_lifecycle,
    "task.license_flagged": _update_task_lifecycle,
    "task.summary_emitted": _update_task_lifecycle,
    # Token-bearing task events — set the gauge in addition to bumping
    # the lifecycle counter.  ``task.step.completed`` and
    # ``task.execution.started`` currently carry no token fields; the
    # token-setter is a no-op for those envelopes (see
    # :func:`_update_task_tokens` docstring).
    "task.execution.started": _update_task_tokens,
    "task.step.completed": _update_task_tokens,
    "task.budget_exceeded": _update_task_tokens,
    # Terminal task events — bump lifecycle counter AND clear the gauge.
    "task.completed": _update_task_lifecycle_and_clear_task_gauge,
    "task.stop_requested": _update_task_lifecycle_and_clear_task_gauge,
    # Session lifecycle.
    "session.started": _update_session_lifecycle,
    "session.heartbeat": _update_session_lifecycle,
    "session.finished": _update_session_lifecycle,
    "session.heartbeat_timeout": _update_session_lifecycle,
    "session.reconnecting": _update_session_lifecycle,
    # Secret access.
    "secret.accessed": _update_secret_accessed,
}


def update_for(state: MetricsState, envelope: EventEnvelope) -> None:
    """Dispatch a single envelope to its metric updater + bump the family counter.

    Story 10.4 AC6/AC7 — called from the ``run_subscriber`` tail loop
    on every envelope.  The dispatch is two-step:

      1. If ``envelope.type`` is in :data:`_DISPATCH`, apply the
         registered updater (mutates the typed counter + optional
         gauge).
      2. Increment ``omb_events_appended_total{event_family=<prefix>}``
         where ``<prefix>`` is the substring before the first dot in
         ``envelope.type``.  Unknown envelope types skip step 1 but
         still register in the family counter so PromQL
         ``rate(omb_events_appended_total[1m])`` reflects the true
         append throughput.

    The ``event_family`` label is bounded by the
    :data:`_EVENT_FAMILIES` enum at registration time (pre-populated
    in :func:`build_collectors`).  A wholly novel family (no
    ``register()`` call, no prefix match) would still call
    ``.labels(...)`` here — but the bounded-enum pre-population +
    Story 10.5's cardinality regression test (≤ 200 timeseries
    bound) catches drift.  Defensive: this is the only place in the
    Story 10.4 surface where an unbounded ``event_family`` value
    could leak; if a future story registers ``foo.bar`` it MUST also
    extend :data:`_EVENT_FAMILIES`.

    Atomicity note: dispatch update + family-counter increment is NOT
    atomic across the two operations.  A concurrent scrape can observe
    the family counter incremented while the typed updater has not yet
    run (or vice-versa).  Sub-microsecond window; well within scrape
    jitter (see ``record_lag`` docstring for the same trade-off).
    """
    updater = _DISPATCH.get(envelope.type)
    if updater is not None:
        updater(state, envelope)
    family = envelope.type.split(".", 1)[0]
    state.events_appended_total.labels(event_family=family).inc()


# ---------------------------------------------------------------------------
# Story 10.3 + 10.4 — registration factory.
# ---------------------------------------------------------------------------


def build_collectors(registry: CollectorRegistry) -> MetricsState:
    """Construct + register the Story 10.3 + 10.4 collectors on *registry*.

    Each collector is registered against the per-app registry so
    ``generate_latest(registry)`` exposes exactly the combined metric
    set — no global ``REGISTRY`` pollution, no test cross-talk.

    Pre-population (Story 10.3 P1-H1 + Story 10.4 AC10): every bounded-
    enum label child is materialised via ``.inc(0)`` at registration
    time.  This eliminates the lazy-registration race between
    ``Counter.labels(...).inc()`` from the worker thread and a
    concurrent ``generate_latest()`` scrape.  Counters: 15 task +
    5 session + 5 actor_kind + 11 event_family + 2 idempotency +
    6 capability + 4 parse_skip = 48 pre-populated children.  Plus
    the 3 label-free gauges + 2 labelled gauges (cursor_offset,
    task_tokens) — total steady-state cardinality ≈ 50 timeseries
    (Story 10.4 AC10 bound).

    Args:
        registry: A fresh :class:`CollectorRegistry` instance owned by
            the FastAPI app (constructed once per lifespan startup).

    Returns:
        A populated :class:`MetricsState` ready to mutate from the
        tail loop.
    """
    # --- Story 10.3 metrics ------------------------------------------------
    lag_seconds = Gauge(
        "metrics_subscriber_lag_seconds",
        "Wall-clock lag of the most recently processed envelope vs now() (seconds).",
        registry=registry,
    )
    bytes_behind = Gauge(
        "metrics_subscriber_bytes_behind",
        "Bytes between today's JSONL file size and the subscriber's cursor offset.",
        registry=registry,
    )
    cursor_offset_bytes = Gauge(
        "metrics_subscriber_cursor_offset_bytes",
        "Current cursor byte offset in the JSONL log (labelled by file path).",
        labelnames=("path",),
        registry=registry,
    )
    parse_skip_total = Counter(
        "metrics_subscriber_parse_skip_total",
        "Lines skipped during JSONL tail, labelled by reason (bounded enum).",
        labelnames=("reason",),
        registry=registry,
    )
    for _reason in ("json_decode", "not_a_dict", "pre110_missing_trace_id", "validation"):
        parse_skip_total.labels(reason=_reason).inc(0)

    # --- Story 10.4 — FR62 core metric set ---------------------------------
    task_lifecycle_total = Counter(
        "omb_task_lifecycle_events_total",
        (
            "Task-lifecycle envelopes observed on the JSONL tail, labelled by "
            "envelope.type (bounded 15-value enum)."
        ),
        labelnames=("event_type",),
        registry=registry,
    )
    for _event_type in _TASK_LIFECYCLE_EVENT_TYPES:
        task_lifecycle_total.labels(event_type=_event_type).inc(0)

    session_lifecycle_total = Counter(
        "omb_session_lifecycle_events_total",
        (
            "Session-lifecycle envelopes observed on the JSONL tail, labelled by "
            "phase (bounded 5-value enum: started, heartbeat, finished, "
            "heartbeat_timeout, reconnecting)."
        ),
        labelnames=("phase",),
        registry=registry,
    )
    for _phase in _SESSION_PHASES:
        session_lifecycle_total.labels(phase=_phase).inc(0)

    secret_accessed_total = Counter(
        "omb_secret_accessed_total",
        (
            "secret.accessed envelopes observed on the JSONL tail, labelled by "
            "actor_kind (bounded enum sourced from events.envelope.ActorKind: "
            "operator, orchestrator, worker, system, clawhip)."
        ),
        labelnames=("actor_kind",),
        registry=registry,
    )
    for _actor_kind in _ACTOR_KINDS:
        secret_accessed_total.labels(actor_kind=_actor_kind).inc(0)

    events_appended_total = Counter(
        "omb_events_appended_total",
        (
            "Total envelopes processed by the tail loop, labelled by "
            "event_family (envelope.type prefix before first dot — bounded "
            "11-value enum).  Use PromQL rate(...) for FR62 1m/5m/1h windows."
        ),
        labelnames=("event_family",),
        registry=registry,
    )
    for _family in _EVENT_FAMILIES:
        events_appended_total.labels(event_family=_family).inc(0)

    task_tokens_spent = Gauge(
        "omb_task_tokens_spent",
        (
            "Per-task cumulative token-spend snapshot.  Labelled by task_id; "
            "cardinality bounded by active-task count + cleanup-on-completion "
            "rule (see ADR-0005 §Cardinality Discipline)."
        ),
        labelnames=("task_id",),
        registry=registry,
    )

    idempotency_cache_total = Counter(
        "omb_idempotency_cache_total",
        (
            "DEFERRED-FROM-FR62 — pending upstream event emission, see "
            "Story 10.4.x / 11.x.  Will count idempotency.cache_hit and "
            "idempotency.factory_ran outcomes once registry-api emits them.  "
            "Pre-registered with zero values to keep operator dashboards "
            "stable."
        ),
        labelnames=("outcome",),
        registry=registry,
    )
    for _outcome in _IDEMPOTENCY_OUTCOMES:
        idempotency_cache_total.labels(outcome=_outcome).inc(0)

    capability_denied_total = Counter(
        "omb_capability_denied_total",
        (
            "DEFERRED-FROM-FR62 — pending upstream event emission, see "
            "Story 10.4.x / 11.x.  Will count capability.denied events "
            "(tier × boundary 6-combination enum) once "
            "TierEnforcementMiddleware emits them.  Pre-registered with "
            "zero values to keep operator dashboards stable."
        ),
        labelnames=("tier", "boundary"),
        registry=registry,
    )
    for _tier in _CAPABILITY_TIERS:
        for _boundary in _CAPABILITY_BOUNDARIES:
            capability_denied_total.labels(tier=_tier, boundary=_boundary).inc(0)

    return MetricsState(
        registry=registry,
        lag_seconds=lag_seconds,
        bytes_behind=bytes_behind,
        cursor_offset_bytes=cursor_offset_bytes,
        parse_skip_total=parse_skip_total,
        task_lifecycle_total=task_lifecycle_total,
        session_lifecycle_total=session_lifecycle_total,
        secret_accessed_total=secret_accessed_total,
        events_appended_total=events_appended_total,
        task_tokens_spent=task_tokens_spent,
        idempotency_cache_total=idempotency_cache_total,
        capability_denied_total=capability_denied_total,
    )


__all__ = ["MetricsState", "build_collectors", "update_for"]
