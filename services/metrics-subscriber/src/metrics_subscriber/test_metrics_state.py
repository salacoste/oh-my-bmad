"""Unit tests for :class:`metrics_subscriber.app.metrics.MetricsState` (Stories 10.3 + 10.4).

Synchronous tests for the dataclass + ``build_collectors`` factory.
No FastAPI overhead — exercises the per-app ``CollectorRegistry`` and
the collectors directly.

ACs covered:

  - Story 10.3 AC5 — gauge construction + ``record_lag`` / ``record_cursor``
    mutations are reflected in ``generate_latest``.
  - Story 10.3 AC6 — ``on_parse_skip`` increments
    ``metrics_subscriber_parse_skip_total{reason=...}`` for the
    bounded-enum reason values.
  - Story 10.3 AC14 (defensive) — fresh ``CollectorRegistry()`` per
    ``build_collectors`` call has zero pre-existing collectors so
    registration is idempotent across test sessions.
  - Story 10.4 AC1 — task lifecycle counter increments per event type.
  - Story 10.4 AC2 — session lifecycle counter increments per phase.
  - Story 10.4 AC3 — secret.accessed counter increments per actor_kind.
  - Story 10.4 AC4 — events_appended counter increments per event_family.
  - Story 10.4 AC5 — task tokens gauge set + cleanup-on-completion.
  - Story 10.4 AC6 — dispatch table coverage (full unit-test surface).
  - Story 10.4 AC8 — DEFERRED preview counters pre-populated at zero.
  - Story 10.4 AC10 — steady-state cardinality bound.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from events import Actor, EventEnvelope
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families
from pydantic import BaseModel

from metrics_subscriber.app.metrics import (
    _DISPATCH,
    _TASK_LIFECYCLE_EVENT_TYPES,
    MetricsState,
    build_collectors,
    update_for,
)


def _metric_value(body: bytes, name: str, labels: dict[str, str] | None = None) -> float | None:
    """Parse Prometheus text body; return the float sample value for *name*.

    Matches by sample name (not family name) — Counter samples in
    Prometheus exposition carry the ``_total`` suffix while the
    parser's family name strips it.  Sample-name matching is the
    canonical comparison.

    When *labels* is supplied, only samples whose labels match exactly
    (subset semantics: every key in *labels* matches the sample) are
    considered.  Returns ``None`` if the metric is absent.
    """
    text = body.decode()
    target_labels = labels or {}
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name != name:
                continue
            if all(sample.labels.get(k) == v for k, v in target_labels.items()):
                return float(sample.value)
    return None


def test_build_collectors_registers_four_metrics() -> None:
    registry = CollectorRegistry()
    state = build_collectors(registry)
    assert isinstance(state, MetricsState)
    body = generate_latest(registry)
    text = body.decode()
    # Counter family appears with the ``_total`` suffix in
    # exposition output; the underlying metric name registered on
    # the Counter object is ``metrics_subscriber_parse_skip_total``
    # which exposition formats as ``metrics_subscriber_parse_skip_total``.
    for name in (
        "metrics_subscriber_lag_seconds",
        "metrics_subscriber_bytes_behind",
        "metrics_subscriber_cursor_offset_bytes",
        "metrics_subscriber_parse_skip_total",
    ):
        assert name in text, f"missing metric: {name}"


def test_record_lag_updates_two_gauges() -> None:
    registry = CollectorRegistry()
    state = build_collectors(registry)
    state.record_lag(lag_seconds=1.5, bytes_behind=42)
    body = generate_latest(registry)
    lag = _metric_value(body, "metrics_subscriber_lag_seconds")
    bytes_b = _metric_value(body, "metrics_subscriber_bytes_behind")
    assert lag == 1.5
    assert bytes_b == 42.0


def test_record_cursor_uses_path_label() -> None:
    registry = CollectorRegistry()
    state = build_collectors(registry)
    today = Path("/tmp/events/2026-05-19.jsonl")
    state.record_cursor(path=today, offset=12345)
    body = generate_latest(registry)
    offset = _metric_value(
        body,
        "metrics_subscriber_cursor_offset_bytes",
        labels={"path": str(today)},
    )
    assert offset == 12345.0


def test_on_parse_skip_increments_counter_by_reason() -> None:
    """AC6 — bounded-enum reasons map to distinct counter labels."""
    registry = CollectorRegistry()
    state = build_collectors(registry)
    state.on_parse_skip("json_decode")
    state.on_parse_skip("json_decode")
    state.on_parse_skip("not_a_dict")
    state.on_parse_skip("validation")
    body = generate_latest(registry)
    assert (
        _metric_value(
            body,
            "metrics_subscriber_parse_skip_total",
            labels={"reason": "json_decode"},
        )
        == 2.0
    )
    assert (
        _metric_value(
            body,
            "metrics_subscriber_parse_skip_total",
            labels={"reason": "not_a_dict"},
        )
        == 1.0
    )
    assert (
        _metric_value(
            body,
            "metrics_subscriber_parse_skip_total",
            labels={"reason": "validation"},
        )
        == 1.0
    )


def test_per_app_registry_is_isolated() -> None:
    """AC14 defensive — two registries do not share collector state."""
    reg_a = CollectorRegistry()
    reg_b = CollectorRegistry()
    state_a = build_collectors(reg_a)
    state_b = build_collectors(reg_b)
    state_a.record_lag(lag_seconds=5.0, bytes_behind=100)
    state_b.record_lag(lag_seconds=99.0, bytes_behind=900)
    body_a = generate_latest(reg_a)
    body_b = generate_latest(reg_b)
    assert _metric_value(body_a, "metrics_subscriber_lag_seconds") == 5.0
    assert _metric_value(body_b, "metrics_subscriber_lag_seconds") == 99.0


# ===========================================================================
# Story 10.4 — FR62 core metric set tests.
# ===========================================================================


# Task lifecycle event types are imported from the metrics module
# (Story 10.4 P1-L1 — avoid duplication drift trap).

_SESSION_TYPES = (
    "session.started",
    "session.heartbeat",
    "session.finished",
    "session.heartbeat_timeout",
    "session.reconnecting",
)


def _make_envelope(
    event_type: str,
    *,
    actor_kind: str = "system",
    payload: dict[str, Any] | BaseModel | None = None,
    event_id: str = "e-01917e5c-a7d1-7000-8abc-000000000000",
) -> EventEnvelope:
    """Construct a minimal valid EventEnvelope for unit tests.

    Bypasses ``EventEnvelope.create`` (no schema registry round-trip);
    the dispatcher unit tests exercise label routing, not envelope
    validation.
    """
    return EventEnvelope(
        event_id=event_id,
        schema_version="1.1.0",
        type=event_type,  # noqa: EVT001 — test helper takes parametric type
        emitted_at=datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC),
        emitted_at_monotonic_ns=1,
        actor=Actor(kind=actor_kind, id="test-actor"),  # type: ignore[arg-type]
        payload=payload if payload is not None else {},
        trace_id="01917e5c-a7d1-7000-8abc-000000000123",
        request_id="01917e5c-a7d1-7000-8abc-000000000456",
    )


# ---------------------------------------------------------------------------
# AC1 — task lifecycle counter.
# ---------------------------------------------------------------------------


def test_task_lifecycle_counter_pre_populated_at_zero() -> None:
    """AC1 — all 15 bounded-enum labels are pre-populated at registration."""
    registry = CollectorRegistry()
    build_collectors(registry)
    body = generate_latest(registry)
    for event_type in _TASK_LIFECYCLE_EVENT_TYPES:
        value = _metric_value(
            body,
            "omb_task_lifecycle_events_total",
            labels={"event_type": event_type},
        )
        assert value == 0.0, f"missing pre-populated label: event_type={event_type}"


def test_task_lifecycle_counter_increments_per_event_type() -> None:
    """AC1 — emit one envelope of each type; each label increments to 1."""
    registry = CollectorRegistry()
    state = build_collectors(registry)
    for event_type in _TASK_LIFECYCLE_EVENT_TYPES:
        # ``task.completed`` / ``task.stop_requested`` go through the
        # gauge-clearing updater — include a benign ``task_id`` so the
        # cleanup path is exercised without crashing.
        env = _make_envelope(event_type, payload={"task_id": f"task-{event_type}"})
        update_for(state, env)
    body = generate_latest(registry)
    for event_type in _TASK_LIFECYCLE_EVENT_TYPES:
        value = _metric_value(
            body,
            "omb_task_lifecycle_events_total",
            labels={"event_type": event_type},
        )
        assert value == 1.0, f"event_type={event_type} did not increment"


# ---------------------------------------------------------------------------
# AC2 — session lifecycle counter.
# ---------------------------------------------------------------------------


def test_session_lifecycle_counter_per_phase() -> None:
    """AC2 — emit one envelope of each session.* type, assert per-phase label."""
    registry = CollectorRegistry()
    state = build_collectors(registry)
    for event_type in _SESSION_TYPES:
        update_for(state, _make_envelope(event_type))
    body = generate_latest(registry)
    for event_type, phase in [
        ("session.started", "started"),
        ("session.heartbeat", "heartbeat"),
        ("session.finished", "finished"),
        ("session.heartbeat_timeout", "heartbeat_timeout"),
        ("session.reconnecting", "reconnecting"),
    ]:
        value = _metric_value(
            body,
            "omb_session_lifecycle_events_total",
            labels={"phase": phase},
        )
        assert value == 1.0, f"phase={phase} (from {event_type}) did not increment"


# ---------------------------------------------------------------------------
# AC3 — secret.accessed counter by actor_kind.
# ---------------------------------------------------------------------------


def test_secret_accessed_counter_by_actor_kind() -> None:
    """AC3 — emit 3 secret.accessed envelopes with different actor.kind.

    Uses the actual ActorKind enum (operator, orchestrator, worker,
    system, clawhip) per Story 10.4 actor-kind spec-drift resolution.
    """
    registry = CollectorRegistry()
    state = build_collectors(registry)
    for kind in ("operator", "system", "worker"):
        update_for(state, _make_envelope("secret.accessed", actor_kind=kind))
    body = generate_latest(registry)
    assert (
        _metric_value(body, "omb_secret_accessed_total", labels={"actor_kind": "operator"}) == 1.0
    )
    assert _metric_value(body, "omb_secret_accessed_total", labels={"actor_kind": "system"}) == 1.0
    assert _metric_value(body, "omb_secret_accessed_total", labels={"actor_kind": "worker"}) == 1.0
    # Untouched kinds remain at the pre-populated zero.
    assert _metric_value(body, "omb_secret_accessed_total", labels={"actor_kind": "clawhip"}) == 0.0


# ---------------------------------------------------------------------------
# AC4 — events_appended counter per family.
# ---------------------------------------------------------------------------


def test_events_appended_counter_per_family() -> None:
    """AC4 — emit envelopes of 3+ different families; per-family counter increments."""
    registry = CollectorRegistry()
    state = build_collectors(registry)
    # task.created counts once for the family.
    update_for(state, _make_envelope("task.created", payload={"task_id": "t1"}))
    update_for(state, _make_envelope("task.created", payload={"task_id": "t2"}))
    update_for(state, _make_envelope("session.started"))
    update_for(state, _make_envelope("secret.accessed"))
    # An unknown type that still has a family prefix.
    update_for(state, _make_envelope("approval.granted"))
    body = generate_latest(registry)
    assert _metric_value(body, "omb_events_appended_total", labels={"event_family": "task"}) == 2.0
    assert (
        _metric_value(body, "omb_events_appended_total", labels={"event_family": "session"}) == 1.0
    )
    assert (
        _metric_value(body, "omb_events_appended_total", labels={"event_family": "secret"}) == 1.0
    )
    assert (
        _metric_value(body, "omb_events_appended_total", labels={"event_family": "approval"}) == 1.0
    )


# ---------------------------------------------------------------------------
# AC5 — per-task token-spend gauge (set + cleanup).
# ---------------------------------------------------------------------------


def test_task_tokens_spent_gauge_set_and_cleared() -> None:
    """AC5 — set gauge on token-bearing envelope, clear on task.completed.

    ``task.budget_exceeded`` carries ``tokens_used``; ``task.completed``
    carries ``token_usage``; both are picked up by
    ``_update_task_tokens`` via the field-name-tolerant
    :func:`_payload_get`.
    """
    registry = CollectorRegistry()
    state = build_collectors(registry)
    task_id = "task-abc-123"
    # Set: task.budget_exceeded carries ``tokens_used`` field.
    update_for(
        state,
        _make_envelope(
            "task.budget_exceeded",
            payload={"task_id": task_id, "tokens_used": 4200},
        ),
    )
    body = generate_latest(registry)
    assert _metric_value(body, "omb_task_tokens_spent", labels={"task_id": task_id}) == 4200.0, (
        "gauge should be set after task.budget_exceeded"
    )
    # Cleanup: task.completed removes the labelled child.
    update_for(state, _make_envelope("task.completed", payload={"task_id": task_id}))
    body = generate_latest(registry)
    assert _metric_value(body, "omb_task_tokens_spent", labels={"task_id": task_id}) is None, (
        "gauge labelled child should be removed after task.completed"
    )


def test_task_completed_logs_final_token_usage_before_gauge_clear() -> None:
    """Story 10.4 P1-H4 — FR44 final ``token_usage`` reading is logged on terminate.

    The gauge is the LIVE view; the structured log entry is the
    canonical historical record (NFR-O1 — events as primary
    observability).  This test asserts the log entry is emitted with
    the final token count BEFORE the gauge is cleared.
    """
    registry = CollectorRegistry()
    state = build_collectors(registry)
    task_id = "task-final-tokens"
    # Set the gauge first so cleanup has something to remove.
    update_for(
        state,
        _make_envelope(
            "task.budget_exceeded",
            payload={"task_id": task_id, "tokens_used": 1234},
        ),
    )
    with structlog.testing.capture_logs() as caps:
        update_for(
            state,
            _make_envelope(
                "task.completed",
                payload={"task_id": task_id, "token_usage": 5678},
            ),
        )
    finals = [c for c in caps if c.get("event") == "metrics_subscriber_task_token_usage_final"]
    assert len(finals) == 1, (
        f"expected exactly one final-token log entry, got {len(finals)}: {caps}"
    )
    entry = finals[0]
    assert entry["task_id"] == task_id
    assert entry["tokens"] == 5678
    assert entry["source"] == "task.completed"
    # Gauge is cleared after the log emission.
    body = generate_latest(registry)
    assert _metric_value(body, "omb_task_tokens_spent", labels={"task_id": task_id}) is None


def test_task_completed_without_token_usage_skips_final_log() -> None:
    """Story 10.4 P1-H4 — missing/None ``token_usage`` is a silent no-op.

    ``task.stop_requested`` and minimal ``task.completed`` payloads
    that omit the token field MUST NOT emit a spurious final-token
    log entry (would corrupt the FR44 historical record with placeholder
    zeros).
    """
    registry = CollectorRegistry()
    state = build_collectors(registry)
    with structlog.testing.capture_logs() as caps:
        update_for(
            state,
            _make_envelope("task.stop_requested", payload={"task_id": "t-no-tokens"}),
        )
    finals = [c for c in caps if c.get("event") == "metrics_subscriber_task_token_usage_final"]
    assert finals == [], "task.stop_requested without token_usage must NOT emit final-token log"


def test_task_tokens_spent_gauge_cleared_by_stop_requested() -> None:
    """AC5 — task.stop_requested is the second terminal event that cleans the gauge."""
    registry = CollectorRegistry()
    state = build_collectors(registry)
    task_id = "task-stop-test"
    update_for(
        state,
        _make_envelope(
            "task.budget_exceeded",
            payload={"task_id": task_id, "tokens_used": 1000},
        ),
    )
    body = generate_latest(registry)
    assert _metric_value(body, "omb_task_tokens_spent", labels={"task_id": task_id}) == 1000.0
    update_for(state, _make_envelope("task.stop_requested", payload={"task_id": task_id}))
    body = generate_latest(registry)
    assert _metric_value(body, "omb_task_tokens_spent", labels={"task_id": task_id}) is None


def test_task_gauge_cleanup_then_resurrect_is_idempotent() -> None:
    """Out-of-order ``task.budget_exceeded`` after terminator does NOT resurrect gauge.

    Story 10.4 P1-H3 — the terminated-task LRU prevents ghost-gauge
    leak.  A ``task.budget_exceeded`` arriving AFTER ``task.completed``
    (clock skew or replay) MUST NOT create a labelled child for that
    task_id — without the guard the gauge would persist with no
    subsequent cleanup, leaking cardinality monotonically.

    Bounded-leak risk: tasks completed more than 10_000 events ago can
    still resurrect a gauge child (LRU evicts oldest task_id).  This
    is documented and accepted in :class:`MetricsState`.
    """
    registry = CollectorRegistry()
    state = build_collectors(registry)
    task_id = "task-out-of-order"
    # Out-of-order: completion before any tokens recorded.
    update_for(state, _make_envelope("task.completed", payload={"task_id": task_id}))
    # Late token-bearing envelope — must be skipped by the LRU guard.
    update_for(
        state,
        _make_envelope(
            "task.budget_exceeded",
            payload={"task_id": task_id, "tokens_used": 99},
        ),
    )
    body = generate_latest(registry)
    assert _metric_value(body, "omb_task_tokens_spent", labels={"task_id": task_id}) is None, (
        "ghost-gauge leak: task_tokens_spent labelled child should remain "
        "REMOVED after task.completed; late task.budget_exceeded must NOT "
        "resurrect it (Story 10.4 P1-H3)"
    )


# ---------------------------------------------------------------------------
# AC6 — dispatch table coverage.
# ---------------------------------------------------------------------------


def test_dispatch_table_size_meets_minimum() -> None:
    """AC6 — exactly 22 entries.

    Breakdown: 10 lifecycle-only + 3 token-bearing + 2 terminal +
    5 session + 1 secret + 1 capability (Story 11.2 P1-H3) = 22.
    """
    assert len(_DISPATCH) == 22, f"dispatch table has {len(_DISPATCH)} entries, expected == 22"


def test_dispatch_table_covers_all_task_lifecycle_event_types() -> None:
    """AC6 — every task lifecycle event type has a dispatch updater."""
    for event_type in _TASK_LIFECYCLE_EVENT_TYPES:
        assert event_type in _DISPATCH, f"missing dispatch for {event_type}"


def test_dispatch_table_covers_all_session_event_types() -> None:
    """AC6 — every session event type has a dispatch updater."""
    for event_type in _SESSION_TYPES:
        assert event_type in _DISPATCH, f"missing dispatch for {event_type}"


def test_dispatch_unknown_envelope_type_only_increments_appended_counter() -> None:
    """AC6 — unknown type updates only events_appended; no other metric raises."""
    registry = CollectorRegistry()
    state = build_collectors(registry)
    # ``unknown.type`` has family ``unknown`` — Story 10.4 P1-H1 added
    # ``"unknown"`` to the bounded ``_EVENT_FAMILIES`` enum as the
    # fallback bucket so this case routes through a PRE-POPULATED label.
    update_for(state, _make_envelope("unknown.type"))
    body = generate_latest(registry)
    # The known task counter family is untouched.
    assert (
        _metric_value(
            body, "omb_task_lifecycle_events_total", labels={"event_type": "task.created"}
        )
        == 0.0
    )
    # The events_appended counter recorded the unknown family.
    value = _metric_value(body, "omb_events_appended_total", labels={"event_family": "unknown"})
    assert value == 1.0


def test_dispatch_capability_denied_increments_counter_by_tier_boundary() -> None:
    """Story 11.2 P1-H3 — capability.denied envelope increments tier/boundary cell.

    Wires DD5 (Epic 10 retro) ``omb_capability_denied_total`` counter to
    the ``capability.denied`` event registered in Story 11.2. Emission is
    deferred to Story 11.2.1; this test validates the dispatch path so
    when emission lands the metric increments correctly.
    """
    registry = CollectorRegistry()
    state = build_collectors(registry)
    update_for(
        state,
        _make_envelope(
            "capability.denied",
            payload={
                "tier": "tier2",
                "boundary": "http",
                "actor_id": "operator-1",
                "attempted_action": "task.create",
            },
        ),
    )
    body = generate_latest(registry)
    value = _metric_value(
        body,
        "omb_capability_denied_total",
        labels={"tier": "tier2", "boundary": "http"},
    )
    assert value == 1.0, "capability.denied should increment {tier=tier2, boundary=http}"
    # Other tier/boundary cells remain at the pre-populated zero.
    assert (
        _metric_value(
            body,
            "omb_capability_denied_total",
            labels={"tier": "tier1", "boundary": "mcp"},
        )
        == 0.0
    )
    # The family counter also incremented under the "capability" bucket.
    assert (
        _metric_value(body, "omb_events_appended_total", labels={"event_family": "capability"})
        == 1.0
    )


def test_dispatch_capability_denied_defensive_skip_on_unknown_tier() -> None:
    """Story 11.2 P1-H3 — out-of-enum tier/boundary skips increment silently.

    Pydantic Literal types prevent this in production; defense-in-depth
    at the subscriber preserves the bounded-enum cardinality contract.
    """
    registry = CollectorRegistry()
    state = build_collectors(registry)
    update_for(
        state,
        _make_envelope(
            "capability.denied",
            payload={
                "tier": "tier99",  # not in _CAPABILITY_TIERS
                "boundary": "http",
                "actor_id": "operator-1",
                "attempted_action": "task.create",
            },
        ),
    )
    body = generate_latest(registry)
    # No labelled child created for {tier=tier99, boundary=http}.
    assert (
        _metric_value(
            body,
            "omb_capability_denied_total",
            labels={"tier": "tier99", "boundary": "http"},
        )
        is None
    ), "out-of-enum tier must NOT materialise a labelled child"
    # The family counter still records the envelope (P1-H1 contract).
    assert (
        _metric_value(body, "omb_events_appended_total", labels={"event_family": "capability"})
        == 1.0
    )


def test_events_appended_unknown_family_falls_through_to_unknown_bucket() -> None:
    """Story 10.4 P1-H1 — out-of-enum prefix routes to the ``"unknown"`` bucket.

    The cardinality contract requires that NO envelope can lazily
    create a new ``event_family`` labelled child.  An envelope with a
    novel family prefix (here ``"novelty"``) must register in the pre-
    populated ``"unknown"`` bucket, NOT create a new ``"novelty"``
    child.
    """
    registry = CollectorRegistry()
    state = build_collectors(registry)
    # ``novelty.event`` has family ``"novelty"`` which is NOT in
    # ``_EVENT_FAMILIES``; the update_for path must route to ``unknown``.
    update_for(state, _make_envelope("novelty.event"))
    body = generate_latest(registry)
    # No ``novelty`` child was created (would be a cardinality leak).
    assert (
        _metric_value(body, "omb_events_appended_total", labels={"event_family": "novelty"}) is None
    ), "novelty family must NOT materialise a labelled child (P1-H1 cardinality contract)"
    # The ``unknown`` bucket absorbed the increment.
    assert (
        _metric_value(body, "omb_events_appended_total", labels={"event_family": "unknown"}) == 1.0
    ), "novelty.event should route to the ``unknown`` fallback bucket"


# ---------------------------------------------------------------------------
# AC8 — DEFERRED preview counters pre-populated at zero.
# ---------------------------------------------------------------------------


def test_deferred_counters_pre_populated_with_zero_values() -> None:
    """AC8 — idempotency + capability deny counters registered at zero."""
    registry = CollectorRegistry()
    build_collectors(registry)
    body = generate_latest(registry)
    # Idempotency: 2 outcomes.
    for outcome in ("cache_hit", "factory_ran"):
        value = _metric_value(body, "omb_idempotency_cache_total", labels={"outcome": outcome})
        assert value == 0.0, f"missing deferred-preview label outcome={outcome}"
    # Capability: 3 tiers × 2 boundaries = 6 combinations.
    for tier in ("tier1", "tier2", "tier3"):
        for boundary in ("mcp", "http"):
            value = _metric_value(
                body,
                "omb_capability_denied_total",
                labels={"tier": tier, "boundary": boundary},
            )
            assert value == 0.0, f"missing deferred-preview label tier={tier} boundary={boundary}"


# ---------------------------------------------------------------------------
# AC10 — steady-state cardinality bound.
# ---------------------------------------------------------------------------


def test_cardinality_at_steady_state_is_bounded() -> None:
    """AC10 — emit 1000 mixed envelopes; canonical timeseries ≤ 51.

    Story 10.4 asserts the steady-state bound as a smoke check.  Story
    10.5 will extend this with a regression test that emits 10K varying
    task_ids and asserts cardinality ≤ 200.

    Steady-state arithmetic (post-task-cleanup):

      Story 10.3 metrics : lag_seconds (1) + bytes_behind (1) +
                           cursor_offset_bytes (0 — no path labels in
                           this test) + parse_skip_total (4) = 6
      Story 10.4 counters: task_lifecycle (15) + session (5) +
                           secret_accessed (5) + events_appended
                           (14 — 11 base families + ``"unknown"`` fallback
                           bucket per P1-H1 + ``"key"`` + ``"capability"``
                           per Story 11.2 P1-H2) + idempotency (2) +
                           capability (6) = 47
      task_tokens_spent  : 0 after cleanup (terminal events ran).

      Total = 53 — bound widened by +2 vs Story 10.4 P1-H1 cardinality
      (was 51) to accommodate the two new Story 11.2 P1-H2 event-family
      pre-populated samples (``"key"`` + ``"capability"``); "timeseries"
      = canonical samples post-``_created`` filter per P1-L4 wording.
    """
    registry = CollectorRegistry()
    state = build_collectors(registry)
    # Round-robin: terminal events ensure task gauge cleanup.
    rotation = (
        ("task.created", "task-1"),
        ("task.execution.started", "task-1"),
        ("task.step.completed", "task-1"),
        ("task.completed", "task-1"),  # cleanup
        ("session.started", None),
        ("session.heartbeat", None),
        ("session.finished", None),
        ("secret.accessed", None),
        ("task.budget_exceeded", "task-2"),
        ("task.stop_requested", "task-2"),  # cleanup
    )
    for i in range(100):  # 1000 envelopes / 10 per rotation
        for event_type, task_id in rotation:
            payload: dict[str, Any] = {}
            if task_id is not None:
                payload["task_id"] = f"{task_id}-{i}"
                if event_type == "task.budget_exceeded":
                    payload["tokens_used"] = 100
            update_for(state, _make_envelope(event_type, payload=payload))
    # P1-M3: explicit assertion on the unbounded-label gauge — must be
    # ZERO labelled children after cleanup.  Relying solely on the
    # total-timeseries bound can hide a few leaked children inside the
    # 50-sample headroom.
    assert len(list(state.task_tokens_spent._metrics)) == 0, (  # noqa: SLF001
        "task_tokens_spent gauge children leaked after cleanup"
    )
    timeseries = list(registry.collect())
    # Each Metric Family is collected once; ``samples`` enumerates
    # per-labelset.  ``prometheus_client`` emits TWO samples per
    # Counter labelset — the ``_total`` value AND a ``_created``
    # timestamp.  "Canonical timeseries" in AC10 = the number of
    # samples Prometheus actually indexes separately, which means
    # filtering the ``_created`` bookkeeping metadata.  Gauge labelsets
    # emit a single sample each.
    canonical_timeseries = sum(
        1
        for family in timeseries
        for sample in family.samples
        if not sample.name.endswith("_created")
    )
    # Story 11.2.3 AC2: bound bumped 53 → 62 to accommodate the new
    # ``omb_event_log_lock_wait_ms`` Histogram (5 explicit buckets
    # [0.1, 1, 10, 100, 1000] + ``+Inf`` + ``_count`` + ``_sum`` = 8
    # series; 9 observed empirically including a sampling sample).
    assert canonical_timeseries <= 62, (
        f"cardinality {canonical_timeseries} exceeds AC10 steady-state bound of 62; "
        f"families: {[(f.name, len(f.samples)) for f in timeseries]}"
    )


def test_cardinality_under_burst_cleanup() -> None:
    """Story 10.4 P1-M3 / P1-L5 — adversarial burst sequence.

    The base steady-state test interleaves token-emitting + terminal
    events 1-for-1 in the same rotation, so cleanup runs in the same
    iteration as ``.set()``.  This variant emits ALL token-bearing
    envelopes FIRST (gauge children pile up), THEN all terminal events
    (cleanup must drain them).  Asserts the unbounded-label gauge is
    empty + the total timeseries bound still holds.
    """
    registry = CollectorRegistry()
    state = build_collectors(registry)
    n_tasks = 50
    # Phase 1 — burst-create gauge children.
    for i in range(n_tasks):
        task_id = f"burst-task-{i}"
        update_for(
            state,
            _make_envelope(
                "task.budget_exceeded",
                payload={"task_id": task_id, "tokens_used": 42 + i},
            ),
        )
    # Mid-burst sanity: all 50 gauge children present.
    assert len(list(state.task_tokens_spent._metrics)) == n_tasks, (  # noqa: SLF001
        "burst phase 1 should have created exactly n_tasks gauge children"
    )
    # Phase 2 — drain via terminators.
    for i in range(n_tasks):
        task_id = f"burst-task-{i}"
        update_for(state, _make_envelope("task.completed", payload={"task_id": task_id}))
    assert len(list(state.task_tokens_spent._metrics)) == 0, (  # noqa: SLF001
        "task_tokens_spent gauge children leaked after burst-cleanup"
    )
    canonical_timeseries = sum(
        1
        for family in registry.collect()
        for sample in family.samples
        if not sample.name.endswith("_created")
    )
    # Story 11.2.3: see steady-state bound rationale above.
    assert canonical_timeseries <= 62, (
        f"cardinality {canonical_timeseries} exceeds AC10 burst-cleanup bound of 62"
    )


def test_steady_state_includes_known_metric_families() -> None:
    """Sanity check — the named Story 10.4 metric families ARE registered."""
    registry = CollectorRegistry()
    build_collectors(registry)
    body = generate_latest(registry)
    text = body.decode()
    for name in (
        "omb_task_lifecycle_events_total",
        "omb_session_lifecycle_events_total",
        "omb_secret_accessed_total",
        "omb_events_appended_total",
        "omb_task_tokens_spent",
        "omb_idempotency_cache_total",
        "omb_capability_denied_total",
    ):
        assert name in text, f"missing metric family: {name}"


# ---------------------------------------------------------------------------
# AC6 graceful-payload — dispatch updaters tolerate missing payload fields.
# ---------------------------------------------------------------------------


def test_task_tokens_updater_handles_missing_payload_field() -> None:
    """AC6 — _update_task_tokens does not crash when payload omits tokens."""
    registry = CollectorRegistry()
    state = build_collectors(registry)
    # task.execution.started has NO token field on the payload model;
    # the updater bumps the lifecycle counter but does not set the
    # gauge.
    update_for(
        state,
        _make_envelope("task.execution.started", payload={"task_id": "t-no-tokens"}),
    )
    body = generate_latest(registry)
    # Lifecycle counter bumped.
    assert (
        _metric_value(
            body,
            "omb_task_lifecycle_events_total",
            labels={"event_type": "task.execution.started"},
        )
        == 1.0
    )
    # Gauge not set — no labelled child.
    assert _metric_value(body, "omb_task_tokens_spent", labels={"task_id": "t-no-tokens"}) is None
