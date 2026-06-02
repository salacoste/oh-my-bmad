"""Unit tests for scripts/replication_lag_detector.py — Story 13.4 (NFR-R7).

The detector is the PURE, hermetic core of ``just litestream-lag-check``: no
docker, no network, no real clock. These tests drive the debounce state machine
through every transition:

  * first sample seeds baseline, never emits
  * no lag (sync_count advancing) → no emit, state reset
  * lag onset (stall + rising errors) but < 5 min → no emit
  * lag sustained > 5 min → emit EXACTLY ONCE
  * repeated calls after emit → no re-emit
  * recovery (sync advances) resets, then a NEW episode emits again
  * stall WITHOUT rising errors → not a lag episode
  * state_from_json coercion round-trip
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "replication_lag_detector.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("replication_lag_detector", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["replication_lag_detector"] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
Sample = _mod.Sample  # type: ignore[attr-defined]
evaluate = _mod.evaluate  # type: ignore[attr-defined]
initial_state = _mod.initial_state  # type: ignore[attr-defined]
state_from_json = _mod.state_from_json  # type: ignore[attr-defined]
DEFAULT_SUSTAINED_THRESHOLD_SECONDS = _mod.DEFAULT_SUSTAINED_THRESHOLD_SECONDS  # type: ignore[attr-defined]
DEFAULT_LAG_THRESHOLD_SECONDS = _mod.DEFAULT_LAG_THRESHOLD_SECONDS  # type: ignore[attr-defined]


def test_first_sample_seeds_baseline_never_emits() -> None:
    result = evaluate(None, Sample(sync_count=100, sync_error_count=0, observed_at_epoch=1000.0))
    assert result.should_emit is False
    assert result.emit_fields is None
    assert result.new_state["lag_onset_epoch"] is None
    assert result.new_state["emitted"] is False
    assert result.new_state["last_sync_count"] == 100
    assert result.new_state["last_sync_error_count"] == 0


def test_sync_advancing_is_not_lag_and_resets() -> None:
    state = initial_state(Sample(sync_count=100, sync_error_count=0, observed_at_epoch=1000.0))
    result = evaluate(state, Sample(sync_count=101, sync_error_count=0, observed_at_epoch=1001.0))
    assert result.should_emit is False
    assert result.new_state["lag_onset_epoch"] is None
    assert result.new_state["emitted"] is False
    assert result.new_state["last_sync_count"] == 101


def test_lag_onset_but_under_threshold_no_emit() -> None:
    # Baseline at t=1000, sync_count=100, errors=0.
    state = initial_state(Sample(sync_count=100, sync_error_count=0, observed_at_epoch=1000.0))
    # Stall (sync_count unchanged) + errors rose at t=1030 → onset, but only 0s
    # sustained on this sample (onset == observed_at).
    result = evaluate(state, Sample(sync_count=100, sync_error_count=1, observed_at_epoch=1030.0))
    assert result.should_emit is False
    assert result.new_state["lag_onset_epoch"] == 1030.0
    assert result.new_state["emitted"] is False
    # A second stall sample at t=1300 → 270s sustained, still < 300 → no emit.
    result2 = evaluate(
        result.new_state, Sample(sync_count=100, sync_error_count=2, observed_at_epoch=1300.0)
    )
    assert result2.should_emit is False
    assert result2.new_state["lag_onset_epoch"] == 1030.0


def test_lag_sustained_over_threshold_emits_once() -> None:
    state = initial_state(Sample(sync_count=100, sync_error_count=0, observed_at_epoch=1000.0))
    # onset at t=1030.
    s1 = evaluate(state, Sample(sync_count=100, sync_error_count=1, observed_at_epoch=1030.0))
    # t=1331 → 301s sustained (> 300) → EMIT.
    s2 = evaluate(
        s1.new_state, Sample(sync_count=100, sync_error_count=3, observed_at_epoch=1331.0)
    )
    assert s2.should_emit is True
    assert s2.emit_fields is not None
    assert s2.emit_fields.signal == "sync_stalled"
    assert s2.emit_fields.threshold_seconds == DEFAULT_LAG_THRESHOLD_SECONDS
    assert s2.emit_fields.sustained_seconds == 301
    assert s2.emit_fields.sync_error_count == 3
    assert s2.new_state["emitted"] is True


def test_no_re_emit_after_emit() -> None:
    state = initial_state(Sample(sync_count=100, sync_error_count=0, observed_at_epoch=1000.0))
    s1 = evaluate(state, Sample(sync_count=100, sync_error_count=1, observed_at_epoch=1030.0))
    s2 = evaluate(
        s1.new_state, Sample(sync_count=100, sync_error_count=3, observed_at_epoch=1331.0)
    )
    assert s2.should_emit is True
    # Further stalls in the SAME episode must not re-emit.
    s3 = evaluate(
        s2.new_state, Sample(sync_count=100, sync_error_count=5, observed_at_epoch=1400.0)
    )
    assert s3.should_emit is False
    s4 = evaluate(
        s3.new_state, Sample(sync_count=100, sync_error_count=9, observed_at_epoch=2000.0)
    )
    assert s4.should_emit is False
    assert s4.new_state["emitted"] is True


def test_recovery_resets_then_new_episode_emits_again() -> None:
    state = initial_state(Sample(sync_count=100, sync_error_count=0, observed_at_epoch=1000.0))
    s1 = evaluate(state, Sample(sync_count=100, sync_error_count=1, observed_at_epoch=1030.0))
    s2 = evaluate(
        s1.new_state, Sample(sync_count=100, sync_error_count=3, observed_at_epoch=1331.0)
    )
    assert s2.should_emit is True
    # Recovery: sync_count advances → reset.
    rec = evaluate(
        s2.new_state, Sample(sync_count=105, sync_error_count=3, observed_at_epoch=1400.0)
    )
    assert rec.should_emit is False
    assert rec.new_state["lag_onset_epoch"] is None
    assert rec.new_state["emitted"] is False
    # NEW episode: stall + rising errors, sustained > 300 → emit AGAIN.
    n1 = evaluate(
        rec.new_state, Sample(sync_count=105, sync_error_count=4, observed_at_epoch=1500.0)
    )
    n2 = evaluate(
        n1.new_state, Sample(sync_count=105, sync_error_count=6, observed_at_epoch=1801.0)
    )
    assert n2.should_emit is True
    assert n2.emit_fields is not None
    assert n2.emit_fields.sustained_seconds == 301


def test_stall_without_rising_errors_is_not_lag() -> None:
    state = initial_state(Sample(sync_count=100, sync_error_count=5, observed_at_epoch=1000.0))
    # sync stalled but errors unchanged → not a lag episode, no onset recorded.
    r = evaluate(state, Sample(sync_count=100, sync_error_count=5, observed_at_epoch=2000.0))
    assert r.should_emit is False
    assert r.new_state["lag_onset_epoch"] is None
    assert r.new_state["emitted"] is False
    assert r.new_state["last_sync_count"] == 100
    assert r.new_state["last_sync_error_count"] == 5


def test_exact_threshold_boundary_is_strict_greater_than() -> None:
    # sustained == 300 exactly must NOT emit (strict > 300).
    state = initial_state(Sample(sync_count=100, sync_error_count=0, observed_at_epoch=1000.0))
    s1 = evaluate(state, Sample(sync_count=100, sync_error_count=1, observed_at_epoch=1030.0))
    s2 = evaluate(
        s1.new_state, Sample(sync_count=100, sync_error_count=2, observed_at_epoch=1330.0)
    )
    # 1330 - 1030 == 300 → not > 300 → no emit.
    assert s2.should_emit is False
    # One more second crosses it.
    s3 = evaluate(
        s2.new_state, Sample(sync_count=100, sync_error_count=3, observed_at_epoch=1331.0)
    )
    assert s3.should_emit is True


def test_custom_thresholds_respected() -> None:
    state = initial_state(Sample(sync_count=1, sync_error_count=0, observed_at_epoch=0.0))
    s1 = evaluate(
        state,
        Sample(sync_count=1, sync_error_count=1, observed_at_epoch=10.0),
        sustained_threshold_seconds=60,
        lag_threshold_seconds=5,
    )
    # 71s sustained > 60 → emit; payload threshold_seconds reflects custom 5.
    s2 = evaluate(
        s1.new_state,
        Sample(sync_count=1, sync_error_count=2, observed_at_epoch=81.0),
        sustained_threshold_seconds=60,
        lag_threshold_seconds=5,
    )
    assert s2.should_emit is True
    assert s2.emit_fields is not None
    assert s2.emit_fields.threshold_seconds == 5
    assert s2.emit_fields.sustained_seconds == 71


def test_state_from_json_round_trip() -> None:
    raw = {
        "lag_onset_epoch": 1030.5,
        "emitted": True,
        "last_sync_count": 100,
        "last_sync_error_count": 3,
    }
    state = state_from_json(raw)
    assert state["lag_onset_epoch"] == 1030.5
    assert state["emitted"] is True
    assert state["last_sync_count"] == 100
    assert state["last_sync_error_count"] == 3


def test_state_from_json_missing_keys_defaults() -> None:
    state = state_from_json({})
    assert state["lag_onset_epoch"] is None
    assert state["emitted"] is False
    assert state["last_sync_count"] == 0
    assert state["last_sync_error_count"] == 0
