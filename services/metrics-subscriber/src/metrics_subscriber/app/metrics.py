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
  | ``omb_event_log_lock_wait_ms``              | Histo   | (none)      |
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

Actor-kind enum (Story 10.4 — drift-proof at the type level):

  ``_ACTOR_KINDS`` is derived from :data:`events.envelope.ActorKind`
  via ``typing.get_args`` so the canonical type is the single source
  of truth.  The 5 values are
  ``{operator, orchestrator, worker, system, clawhip}`` per the
  envelope schema.  Story 10.4 spec AC3 enumerated a three-bucket
  projection (``{human, system, agent}``) which was a documentation
  artifact; the spec was amended after pass-1 P1-H2 to match the
  canonical type.  A startup ``assert`` in :func:`build_collectors`
  re-checks the set equality so a type-alias refactor in the
  ``events`` package cannot drift this subscriber silently.
"""

from __future__ import annotations

import contextlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, get_args

import structlog
from events import EventEnvelope
from events.envelope import ActorKind
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Bounded-enum label sets (Story 10.4 — pre-populated at build_collectors).
#
# Cross-checked against ``services/registry-state/src/registry_state/domain/
# event_types.py`` ``register()`` calls (Story 10.3 P1-M1 lesson — spec
# enums MUST match actual registrations).  See module docstring for the
# actor_kind spec-drift note.
# ---------------------------------------------------------------------------

#: Bounded enum of task-lifecycle envelope ``type`` values that increment
#: ``omb_task_lifecycle_events_total``.  16 values (15 per Story 10.4 AC1 +
#: ``task.budget_enforcement_triggered`` per Story 12.2 / FR67) — cross-checked
#: against ``event_types.py``.
#:
#: Story 12.2 (FR67): the Epic 12 acceptance gate calls for counting
#: ``task.budget_enforcement_triggered``. This module's idiom is the bounded
#: ``omb_task_lifecycle_events_total{event_type=...}`` counter (one
#: pre-registered label per enum value, no lazy cardinality leak — Story 10.5
#: regression test enforces). So the enforcement event is counted as
#: ``omb_task_lifecycle_events_total{event_type="task.budget_enforcement_triggered"}``
#: rather than a separate ``task_budget_enforcement_triggered_total`` counter,
#: keeping the cardinality contract intact.
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
    "task.budget_enforcement_triggered",
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
#:
#: Story 10.4 pass-1 P1-H2: derived from :data:`events.envelope.ActorKind`
#: via :func:`typing.get_args` so spec/impl drift is impossible at
#: runtime.  A startup assertion in :func:`build_collectors` verifies the
#: set equality.
_ACTOR_KINDS: Final[tuple[str, ...]] = tuple(get_args(ActorKind))

#: O(1) membership-test set companion for ``_ACTOR_KINDS`` — used by
#: :func:`_update_secret_accessed` to defensively skip unknown actor kinds
#: (Story 10.4 P1-H2).
_ACTOR_KINDS_SET: Final[frozenset[str]] = frozenset(_ACTOR_KINDS)

#: Bounded enum of event families (envelope.type prefix before first dot).
#: Story 10.4 P1-H1: includes ``"approval"`` (was missing despite being
#: registered in ``event_types.py:242-245``) and an ``"unknown"`` fallback
#: bucket so :func:`update_for` can never create an unbounded label child.
#: Currently registered families: agent, approval, file, secret, service,
#: session, sink, task, telegram, tier3.  ``deployment`` is included for
#: forward-compatibility per the AC4 enum table (no registered events yet
#: — counter sample stays at 0 until a future story adds the event
#: family).  ``unknown`` is the fallback bucket for any envelope whose
#: prefix is not in the enum.
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
    "key",  # Story 11.2 — key.rotated (FR65a)
    "capability",  # Story 11.2 — capability.denied (DD5 from Epic 10 retro)
    "budget",  # Story 12.3 — budget.override @1.1.0 registered (FR68, D1=(A));
    #            counter stays at 0 until the decisions route switches its emit
    #            from tier3.budget_override to budget.override (consumer migration).
    "unknown",
)

#: O(1) membership-test set companion for ``_EVENT_FAMILIES`` — used by
#: :func:`update_for` to gate the ``event_family`` label against the
#: bounded enum.  Unknown prefixes fall through to the ``"unknown"``
#: bucket (Story 10.4 P1-H1).
_EVENT_FAMILIES_SET: Final[frozenset[str]] = frozenset(_EVENT_FAMILIES)

#: Bounded enum of idempotency-cache outcomes (Story 10.4 AC8 — DEFERRED).
_IDEMPOTENCY_OUTCOMES: Final[tuple[str, ...]] = (
    "cache_hit",
    "factory_ran",
)

#: Bounded enum of capability tiers (Story 10.4 AC8 — DEFERRED).
_CAPABILITY_TIERS: Final[tuple[str, ...]] = ("tier1", "tier2", "tier3")

#: Bounded enum of capability boundaries (Story 10.4 AC8 — DEFERRED).
_CAPABILITY_BOUNDARIES: Final[tuple[str, ...]] = ("mcp", "http")

#: Story 11.2.3 AC2 — buckets for ``omb_event_log_lock_wait_ms`` Histogram.
#: OQ-2 default: ``[0.1, 1, 10, 100, 1000]`` ms covers uncontended (<0.1ms)
#: through pathologically contended (>1s) regimes. Tune via local timing
#: measurements if the production lock-wait distribution clusters elsewhere.
_EVENT_LOG_LOCK_WAIT_BUCKETS: Final[tuple[float, ...]] = (0.1, 1.0, 10.0, 100.0, 1000.0)

#: O(1) membership-test set companions for the capability enums — used by
#: :func:`_update_capability_denied` (Story 11.2 P1-H3) to defensively
#: skip out-of-enum tier/boundary values (Pydantic validation upstream
#: prevents this in production; defense-in-depth at the subscriber).
_CAPABILITY_TIERS_SET: Final[frozenset[str]] = frozenset(_CAPABILITY_TIERS)
_CAPABILITY_BOUNDARIES_SET: Final[frozenset[str]] = frozenset(_CAPABILITY_BOUNDARIES)


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
        event_log_lock_wait_ms: Histogram — Story 11.2.3 AC2.
            Pre-registered with buckets [0.1, 1, 10, 100, 1000] ms and a
            single ``observe(0.0)`` so the metric name appears in the
            scrape surface before any real lock-wait sample arrives.
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
    # Story 11.2.3 AC2 — EventLogWriter fcntl.flock lock-wait observability.
    event_log_lock_wait_ms: Histogram
    # Story 10.4 P1-H3 — bounded LRU of terminated task_ids used by
    # :func:`_update_task_tokens` to prevent the ghost-gauge regression
    # (out-of-order ``task.budget_exceeded`` after the terminal event
    # would otherwise resurrect a labelled child with no cleanup).  Set
    # mirrors the deque for O(1) membership; deque is the sliding
    # bounded window — see :func:`_remember_terminated_task` for the
    # eviction-sync invariant.  Bounded-leak risk for tasks completed
    # more than 10_000 events ago is accepted (documented at
    # :func:`_update_task_lifecycle_and_clear_task_gauge`).
    _terminated_task_ids: deque[str] = field(default_factory=lambda: deque(maxlen=10_000))
    _terminated_task_ids_set: set[str] = field(default_factory=set)

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


def _remember_terminated_task(state: MetricsState, task_id: str) -> None:
    """Append *task_id* to the bounded LRU + sync the O(1) membership set.

    Eviction invariant (Story 10.4 P1-H3): when the deque is at its
    ``maxlen`` and a new item is appended, the oldest item is evicted
    automatically.  We capture the eviction here by checking ``len()``
    before the append and removing the to-be-evicted item from the
    companion set so deque and set stay in lock-step.
    """
    terminated_deque = state._terminated_task_ids
    terminated_set = state._terminated_task_ids_set
    # If the deque is full, the upcoming append evicts ``deque[0]``.
    # Sync the set BEFORE the append so a concurrent reader never sees
    # an evicted ID still in the set (single-thread tail loop so this
    # is belt-and-braces, but the invariant is easier to reason about).
    if terminated_deque.maxlen is not None and len(terminated_deque) == terminated_deque.maxlen:
        evicted = terminated_deque[0]
        terminated_set.discard(evicted)
    terminated_deque.append(task_id)
    terminated_set.add(task_id)


def _update_task_lifecycle_and_clear_task_gauge(
    state: MetricsState, envelope: EventEnvelope
) -> None:
    """Increment the lifecycle counter + remove the per-task token gauge.

    Wired for ``task.completed`` and ``task.stop_requested`` — the two
    envelope types that terminate a task.  The gauge cleanup is the
    load-bearing cardinality invariant (Story 10.4 AC5): without it the
    ``task_id`` label set would grow unbounded.

    Story 10.4 P1-H4 — final ``token_usage`` reading is logged via
    structlog BEFORE the gauge is removed so the FR44 retrospective-
    observability requirement is satisfied through the event-log
    surface (NFR-O1: events as primary observability).  The gauge is
    the LIVE view; the structured log entry is the historical record.

    Story 10.4 P1-H3 — after ``.remove()`` the task_id is appended to
    a bounded LRU (``_terminated_task_ids``).  ``_update_task_tokens``
    consults this LRU and skips ``.set()`` for any task_id present —
    preventing the ghost-gauge regression where an out-of-order
    ``task.budget_exceeded`` after the terminal event would otherwise
    resurrect the labelled child with no subsequent cleanup.  Bounded-
    leak risk: a task whose final token-bearing envelope arrives more
    than 10_000 envelopes after its terminator can still resurrect the
    gauge; documented and accepted in :data:`MetricsState`.

    Atomicity note (Story 10.3 P1-H3 honest claim): the increment and
    the ``.remove()`` are TWO operations — between them a concurrent
    scrape can observe the incremented counter while the gauge child
    is still present.  This is fine: dashboards aggregate over time
    windows that dwarf the microsecond-scale window.
    """
    state.task_lifecycle_total.labels(event_type=envelope.type).inc()
    task_id = _payload_get(envelope, "task_id")
    if not (isinstance(task_id, str) and task_id):
        return
    # P1-H4: emit the final token_usage reading to the structured log
    # BEFORE clearing the gauge.  ``TaskCompletedPayload.token_usage``
    # is the canonical FR44 cumulative count; ``task.stop_requested``
    # does not carry one but we still try the field for forward-
    # compatibility (no-op on absent / wrong-typed values).
    tokens = _payload_get(envelope, "token_usage")
    if isinstance(tokens, int) and tokens >= 0:
        log.info(
            "metrics_subscriber_task_token_usage_final",
            task_id=task_id,
            tokens=tokens,
            source=envelope.type,
        )
    # ``Gauge.remove(...)`` raises ``KeyError`` if no labelled child
    # exists for this task — out-of-order ``task.completed`` before
    # any token-emitting envelope is benign and must not crash the
    # updater.
    with contextlib.suppress(KeyError):
        state.task_tokens_spent.remove(task_id)
    # P1-H3: remember this task as terminated so any subsequent token-
    # bearing envelope (out-of-order ``task.budget_exceeded`` etc.)
    # cannot resurrect the gauge child.
    _remember_terminated_task(state, task_id)


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

    Story 10.4 P1-H3 — a task_id that has already been terminated
    (present in :attr:`MetricsState._terminated_task_ids_set`) is
    silently skipped.  Without this guard, an out-of-order
    ``task.budget_exceeded`` arriving after ``task.completed`` would
    resurrect the gauge child with no subsequent cleanup, leaking
    cardinality monotonically over the deployment lifetime.

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
    # P1-H3 — terminated tasks are not allowed to resurrect a gauge
    # child.  Bounded by the 10_000-entry LRU; out-of-order events that
    # arrive far enough behind the terminator are accepted as a
    # documented bounded-leak risk.
    if task_id in state._terminated_task_ids_set:
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

    Story 10.4 P1-H2 — defensive guard against unknown actor_kind values
    leaking through a schema_version drift that adds a new ActorKind
    Literal value without updating this subscriber.  An unknown kind is
    logged at WARNING and the increment is skipped — preserves the
    bounded-enum cardinality contract for ``actor_kind``.
    """
    kind = envelope.actor.kind
    if kind not in _ACTOR_KINDS_SET:
        log.warning(
            "metrics_subscriber_unknown_actor_kind",
            actor_kind=kind,
            event_type=envelope.type,
            event_id=envelope.event_id,
        )
        return
    state.secret_accessed_total.labels(actor_kind=kind).inc()


def _update_capability_denied(state: MetricsState, envelope: EventEnvelope) -> None:
    """Increment ``omb_capability_denied_total{tier, boundary}`` from ``capability.denied`` payload.

    Story 11.2 P1-H3 — wires DD5 (Epic 10 retro) capability counter to the
    `capability.denied` event registered in this same story. Story 10.4
    pre-populated 6 zero-value combinations; emission is deferred to
    Story 11.2.1 (capability.denied emission follow-up).

    Defensive: skip silently if ``tier`` / ``boundary`` fall outside the
    Story 10.4 bounded enums. Pydantic ``CapabilityDeniedPayload`` Literal
    types enforce the invariant upstream in production; the guard preserves
    bounded-enum cardinality contract under schema_version drift.
    """
    tier = _payload_get(envelope, "tier")
    boundary = _payload_get(envelope, "boundary")
    if tier in _CAPABILITY_TIERS_SET and boundary in _CAPABILITY_BOUNDARIES_SET:
        state.capability_denied_total.labels(tier=tier, boundary=boundary).inc()


#: Story 10.4 AC6 — immutable event_type → updater dispatch table.
#:
#: Keys: 16 task lifecycle (15 + task.budget_enforcement_triggered, Story 12.2)
#: + 5 session + 1 secret + 1 capability.denied = 23 entries.
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
    # Story 12.2 (FR67) — budget-enforcement is terminal (task → failed):
    # count it AND clear the token gauge (the task is done, no live spend).
    "task.budget_enforcement_triggered": _update_task_lifecycle_and_clear_task_gauge,
    # Session lifecycle.
    "session.started": _update_session_lifecycle,
    "session.heartbeat": _update_session_lifecycle,
    "session.finished": _update_session_lifecycle,
    "session.heartbeat_timeout": _update_session_lifecycle,
    "session.reconnecting": _update_session_lifecycle,
    # Secret access.
    "secret.accessed": _update_secret_accessed,
    # Capability denial (Story 11.2 P1-H3 — wires DD5 counter; emission in 11.2.1).
    "capability.denied": _update_capability_denied,
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
    in :func:`build_collectors`).  Story 10.4 P1-H1 — the prefix
    derived from ``envelope.type`` is checked against
    :data:`_EVENT_FAMILIES_SET`; unknown prefixes fall through to the
    pre-populated ``"unknown"`` bucket so no novel labelled child can
    ever be lazily created from the tail loop.  If a future story
    registers an event in a new family it MUST also extend
    :data:`_EVENT_FAMILIES`; until then the envelope still registers
    in the ``"unknown"`` counter for accurate append-rate computation.

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
    # P1-H1: gate the label value against the bounded enum so no
    # unknown prefix can lazily materialise a new labelled child.
    if family not in _EVENT_FAMILIES_SET:
        family = "unknown"
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
    concurrent ``generate_latest()`` scrape.  Counters: 16 task (incl.
    ``task.budget_enforcement_triggered`` per Story 12.2 FR67) +
    5 session + 5 actor_kind + 15 event_family (14 registered families
    incl. ``"budget"`` per Story 12.3 FR68 + 1 ``"unknown"`` fallback
    bucket per Story 10.4 P1-H1) + 2 idempotency +
    6 capability + 4 parse_skip = 53 pre-populated children.  Plus
    the label-free + labelled gauges and the lock-wait Histogram — the
    authoritative steady-state bound is the AC10 cardinality tests
    (``test_metrics_state.py``), currently ≤ 63 timeseries (Story 12.3;
    was ≤ 50 at Story 10.4 AC10, widened by later stories' label adds).

    Args:
        registry: A fresh :class:`CollectorRegistry` instance owned by
            the FastAPI app (constructed once per lifespan startup).

    Returns:
        A populated :class:`MetricsState` ready to mutate from the
        tail loop.
    """
    # Story 10.4 P1-H2 — startup invariant: ``_ACTOR_KINDS`` is derived
    # from :data:`events.envelope.ActorKind` via ``get_args``, but
    # guard against an upstream type-alias refactor that silently
    # changes the type without re-running this module's import.  A
    # failed assertion here means the metrics-subscriber must be
    # rebuilt against the new envelope schema.
    assert set(_ACTOR_KINDS) == set(get_args(ActorKind)), (  # noqa: S101
        f"_ACTOR_KINDS drift detected: {set(_ACTOR_KINDS)} != "
        f"{set(get_args(ActorKind))} — events.envelope.ActorKind changed; "
        "rebuild metrics-subscriber."
    )
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

    # Story 11.2.3 AC2 — EventLogWriter fcntl.flock(LOCK_EX) lock-wait
    # latency observability. The writer (services/registry-state/.../
    # event_log.py) is the only producer; it currently does not push
    # samples cross-process because the writer process IS clawhip-bridge,
    # which has no scrape endpoint of its own. Pre-registration here keeps
    # operator dashboards stable for the day the cross-process bridge
    # ships (Story 11.2.x follow-up). For now, the histogram is observable
    # only if registry-api (which hosts metrics-subscriber) ever instantiates
    # an EventLogWriter with this histogram wired as the
    # ``lock_wait_observer`` callback. Buckets are the OQ-2 default
    # (see ``_EVENT_LOG_LOCK_WAIT_BUCKETS``).
    event_log_lock_wait_ms = Histogram(
        "omb_event_log_lock_wait_ms",
        (
            "Time spent waiting for the EventLogWriter fcntl.flock(LOCK_EX) "
            "inter-process lock, in milliseconds. Story 11.2.3 AC2 — closes "
            "the FR26 multi-writer concern by serializing concurrent "
            "clawhip-bridge subprocess writers. Pre-registered with buckets "
            "[0.1, 1, 10, 100, 1000] ms (OQ-2 default)."
        ),
        buckets=_EVENT_LOG_LOCK_WAIT_BUCKETS,
        registry=registry,
    )
    # Story 10.4 pre-population pattern (mirror of capability_denied_total):
    # observe a single 0.0 sample so the metric NAME appears in
    # ``generate_latest()`` even before any real write has occurred.
    # Operator dashboards depending on the metric name therefore see a
    # stable surface from the first scrape.
    event_log_lock_wait_ms.observe(0.0)

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
        event_log_lock_wait_ms=event_log_lock_wait_ms,
    )


__all__ = ["MetricsState", "build_collectors", "update_for"]
