"""Prometheus metric collectors for the β metrics-subscriber (Story 10.3 AC5+AC6).

The `MetricsState` dataclass is the single mutable cell that the tail
loop (`metrics_subscriber.__main__.run_subscriber`) updates on every
``maybe_persist`` cycle.  The FastAPI ``/metrics`` route reads the same
``CollectorRegistry`` so HTTP scrapes always observe the most recent
gauge / counter values.

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

Metric inventory (Story 10.3 scope — Story 10.4 extends):

  +---------------------------------------------+--------+----------+
  | Metric                                      | Type   | Labels   |
  +---------------------------------------------+--------+----------+
  | ``metrics_subscriber_lag_seconds``          | Gauge  | (none)   |
  | ``metrics_subscriber_bytes_behind``         | Gauge  | (none)   |
  | ``metrics_subscriber_cursor_offset_bytes``  | Gauge  | path     |
  | ``metrics_subscriber_parse_skip_total``     | Counter| reason   |
  +---------------------------------------------+--------+----------+

Cardinality discipline (P2-I3): ``path`` is bounded to today /
yesterday (≤ 2 active values during day-rollover); ``reason`` is a
finite enum over ``{json_decode, not_a_dict, validation,
pre110_missing_trace_id, unknown_event_type, ...}`` extended by the
:mod:`events.log_reader` parse paths.  Story 10.5 will gate
cardinality regressions; Story 10.3 enforces discipline locally.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from prometheus_client import CollectorRegistry, Counter, Gauge


@dataclass(slots=True)
class MetricsState:
    """In-process holder for the Story 10.3 metric collectors.

    One instance per ``FastAPI`` app — created in lifespan startup,
    stored on ``app.state.metrics``, mutated by the tail loop, read
    by the ``/metrics`` HTTP handler via the registry it shares with
    the app.

    Attributes:
        registry: The per-app :class:`prometheus_client.CollectorRegistry`
            shared with the ``/metrics`` route.
        lag_seconds: Gauge — wall-clock lag of the most recently
            processed envelope vs ``datetime.now(UTC)`` (Story 10.2
            VH-2 datetime arithmetic; NTP-sync assumed).
        bytes_behind: Gauge — bytes between today's JSONL file size
            and the subscriber's cursor offset at the most recent
            persist (snapshot, not a running average).
        cursor_offset_bytes: Gauge — current cursor offset, labelled
            by ``path`` for day-rollover introspection (label
            cardinality bounded by today/yesterday).
        parse_skip_total: Counter — lines skipped during JSONL tail,
            labelled by ``reason`` (bounded enum).  Reserved by
            Story 10.2 VH-13 preview field; wired here.
    """

    registry: CollectorRegistry
    lag_seconds: Gauge
    bytes_behind: Gauge
    cursor_offset_bytes: Gauge
    parse_skip_total: Counter

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


def build_collectors(registry: CollectorRegistry) -> MetricsState:
    """Construct + register the Story 10.3 collectors on *registry*.

    Each collector is registered against the per-app registry so
    ``generate_latest(registry)`` exposes exactly the Story 10.3 metric
    set — no global ``REGISTRY`` pollution, no test cross-talk.

    Args:
        registry: A fresh :class:`CollectorRegistry` instance owned by
            the FastAPI app (constructed once per lifespan startup).

    Returns:
        A populated :class:`MetricsState` ready to mutate from the
        tail loop.
    """
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
    # P1-H1: pre-populate the four known reason children at registration
    # time so subsequent ``.labels(reason=...)`` calls from the worker
    # thread are pure dict-lookups (no lazy registration race against a
    # concurrent ``generate_latest()`` scrape).  See Story 10.3 review
    # findings P1-H1 + P1-M1 for the full enum.  ``.inc(0)`` is the
    # canonical idiom to materialise a child without bumping the value.
    for _reason in ("json_decode", "not_a_dict", "pre110_missing_trace_id", "validation"):
        parse_skip_total.labels(reason=_reason).inc(0)
    return MetricsState(
        registry=registry,
        lag_seconds=lag_seconds,
        bytes_behind=bytes_behind,
        cursor_offset_bytes=cursor_offset_bytes,
        parse_skip_total=parse_skip_total,
    )


__all__ = ["MetricsState", "build_collectors"]
