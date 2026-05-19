"""Unit tests for :class:`metrics_subscriber.app.metrics.MetricsState` (Story 10.3).

Synchronous tests for the dataclass + ``build_collectors`` factory.
No FastAPI overhead — exercises the per-app ``CollectorRegistry`` and
the four collectors directly.

ACs covered:

  - AC5 — gauge construction + ``record_lag`` / ``record_cursor``
    mutations are reflected in ``generate_latest``.
  - AC6 — ``on_parse_skip`` increments
    ``metrics_subscriber_parse_skip_total{reason=...}`` for the
    bounded-enum reason values.
  - AC14 (defensive) — fresh ``CollectorRegistry()`` per ``build_collectors``
    call has zero pre-existing collectors so registration is idempotent
    across test sessions.
"""

from __future__ import annotations

from pathlib import Path

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.parser import text_string_to_metric_families

from metrics_subscriber.app.metrics import MetricsState, build_collectors


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
