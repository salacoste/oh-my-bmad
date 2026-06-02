#!/usr/bin/env python3
"""Pure debounce state machine for litestream replication-lag detection (Story 13.4 / NFR-R7).

This module is the UNIT-TESTABLE CORE of ``just litestream-lag-check``. It has NO
I/O, NO network, NO clock — every input (the two litestream counters and the
observation time) is passed in explicitly, and the function returns the next
persisted state plus an emit decision. ``scripts/check_replication_lag.py`` wraps
it with the impure parts (HTTP GET of ``/metrics``, state-file load/save, event
append).

**Lag signal** (works for ANY litestream replica type — Story 13.4 authoritative
findings): replication is "lagging" when ``litestream_sync_count`` did NOT
advance between two successive samples (the sync loop has stalled; it normally
ticks ~1/s) AND ``litestream_sync_error_count`` has risen (errors are being
recorded). ``litestream_replica_operation_total{operation="PUT"}`` is NOT
instrumented for ``file`` replicas, so it is deliberately not consulted.

**Debounce / emit-once-per-episode contract**:

  * Track the lag ONSET time (first sample where the stall+error condition holds).
  * Emit ``replication.lagging`` EXACTLY ONCE per lag episode, when the lag has
    been sustained STRICTLY LONGER than ``sustained_threshold_seconds`` (300s =
    5 min) and the episode has not already emitted.
  * When ``sync_count`` advances again (recovery), RESET the state so a future
    episode can emit again.

The ``threshold_seconds`` field carried on the emitted payload is the ~30s
sampling/lag granularity (the recipe samples ~30s apart); it is informational
metadata about the detection resolution, distinct from the 300s sustained-lag
window that gates emission.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

#: Default sustained-lag window (seconds): emit only after the stall has
#: persisted STRICTLY LONGER than this. 300s = 5 min per NFR-R7 / the recipe.
DEFAULT_SUSTAINED_THRESHOLD_SECONDS = 300

#: Default lag/sampling granularity (seconds) reported on the payload's
#: ``threshold_seconds`` field. The recipe samples ~30s apart.
DEFAULT_LAG_THRESHOLD_SECONDS = 30


class DetectorState(TypedDict):
    """JSON-serializable persisted debounce state for one database.

    Persisted between ``check_replication_lag.py`` invocations as a small JSON
    object. ``None`` (no prior state) is represented by the absence of the file.

    Keys:
      lag_onset_epoch: epoch seconds of the first sample in the current lag
        episode, or ``None`` when not currently lagging.
      emitted: whether ``replication.lagging`` has already been emitted for the
        current episode (prevents re-emit until recovery resets the state).
      last_sync_count: the ``litestream_sync_count`` from the previous sample.
      last_sync_error_count: the ``litestream_sync_error_count`` from the
        previous sample.
      onset_sync_error_count: the ``litestream_sync_error_count`` captured at the
        START of the current stall episode (Story 13.4a). At emit time, comparing
        the current error count against this baseline classifies the signal:
        errors accumulated during the stall → ``"sync_stalled"`` (S3/network
        failures); errors flat throughout → ``"silent_stall"`` (the sync loop
        itself is hung). Unused (carries the last value) when no episode is
        active.
    """

    lag_onset_epoch: float | None
    emitted: bool
    last_sync_count: int
    last_sync_error_count: int
    onset_sync_error_count: int


@dataclass(frozen=True)
class Sample:
    """One observation of the litestream counters for a single database.

    Attributes:
        sync_count: current ``litestream_sync_count{db=...}`` value.
        sync_error_count: current ``litestream_sync_error_count{db=...}`` value.
        observed_at_epoch: wall-clock epoch seconds when the sample was taken.
    """

    sync_count: int
    sync_error_count: int
    observed_at_epoch: float


@dataclass(frozen=True)
class EmitFields:
    """The payload-shaping fields returned when an emit should occur.

    Maps 1:1 onto :class:`events.types.replication.ReplicationLaggingPayload`
    (minus ``db``, which the caller supplies from its configuration). Pure: the
    detector computes these from the sample + state; the caller builds the
    envelope.
    """

    signal: str
    threshold_seconds: int
    sustained_seconds: int
    sync_error_count: int


@dataclass(frozen=True)
class DetectorResult:
    """Result of a single :func:`evaluate` call.

    Attributes:
        new_state: the state to persist for the next invocation (always set).
        should_emit: ``True`` iff a ``replication.lagging`` event must be emitted
            for this sample (the sustained-lag threshold was crossed for the
            first time this episode).
        emit_fields: the payload fields when ``should_emit`` is ``True``; ``None``
            otherwise.
    """

    new_state: DetectorState
    should_emit: bool
    emit_fields: EmitFields | None


def initial_state(sample: Sample) -> DetectorState:
    """Build the bootstrap state from the FIRST observed sample.

    With no prior state there is no previous sample to compare against, so the
    first observation can never be classified as lagging (a stall is defined
    relative to the previous sample). This seeds ``last_sync_count`` /
    ``last_sync_error_count`` so the SECOND invocation has a baseline.
    """
    return DetectorState(
        lag_onset_epoch=None,
        emitted=False,
        last_sync_count=sample.sync_count,
        last_sync_error_count=sample.sync_error_count,
        onset_sync_error_count=sample.sync_error_count,
    )


def evaluate(
    state: DetectorState | None,
    sample: Sample,
    *,
    sustained_threshold_seconds: int = DEFAULT_SUSTAINED_THRESHOLD_SECONDS,
    lag_threshold_seconds: int = DEFAULT_LAG_THRESHOLD_SECONDS,
) -> DetectorResult:
    """Advance the debounce state machine by one sample. PURE — no I/O.

    Args:
        state: the prior persisted state, or ``None`` on the very first run.
        sample: the current observation.
        sustained_threshold_seconds: emit only after the stall has persisted
            STRICTLY LONGER than this (default 300s).
        lag_threshold_seconds: the sampling/lag granularity reported on the
            emitted payload's ``threshold_seconds`` field (default 30s).

    Returns:
        A :class:`DetectorResult` carrying the next state, the emit decision, and
        the payload fields when emitting.
    """
    if state is None:
        # First-ever observation: seed the baseline, never emit (no prior sample
        # to detect a stall against).
        return DetectorResult(
            new_state=initial_state(sample),
            should_emit=False,
            emit_fields=None,
        )

    sync_advanced = sample.sync_count > state["last_sync_count"]

    if sync_advanced:
        # Recovery (or normal progress): the sync loop is making progress again.
        # RESET the episode so a future stall can emit again.
        return DetectorResult(
            new_state=DetectorState(
                lag_onset_epoch=None,
                emitted=False,
                last_sync_count=sample.sync_count,
                last_sync_error_count=sample.sync_error_count,
                onset_sync_error_count=sample.sync_error_count,
            ),
            should_emit=False,
            emit_fields=None,
        )

    # sync_count did NOT advance → a STALL episode is active (Story 13.4a).
    #
    # litestream's sync loop runs every ~1s and bumps ``sync_count`` on EVERY
    # cycle, independent of DB write activity — empirically verified (an idle DB
    # still advances sync_count ~1/s). So a flat sync_count means the sync loop
    # itself stopped; this is a real stall whether or not errors are rising, with
    # NO idle false-positives. The episode therefore starts on ANY stall; the
    # SIGNAL is derived at emit time from whether errors accumulated since onset:
    #   * errors rose during the stall  → "sync_stalled" (S3/network failures)
    #   * errors flat throughout        → "silent_stall" (the loop is hung —
    #     the dangerous silent failure Story 13.4's AND-only signal missed).
    onset = state["lag_onset_epoch"]
    onset_error_count = state["onset_sync_error_count"]
    if onset is None:
        # Episode begins now. Baseline the error count at the value from JUST
        # BEFORE the stall (the previous sample) so any errors that rise during
        # the episode — including this first stalled tick — count toward
        # "sync_stalled".
        onset = sample.observed_at_epoch
        onset_error_count = state["last_sync_error_count"]

    sustained = sample.observed_at_epoch - onset
    already_emitted = state["emitted"]
    cross_threshold = sustained > sustained_threshold_seconds

    should_emit = cross_threshold and not already_emitted
    emit_fields: EmitFields | None = None
    if should_emit:
        signal = "sync_stalled" if sample.sync_error_count > onset_error_count else "silent_stall"
        emit_fields = EmitFields(
            signal=signal,
            threshold_seconds=lag_threshold_seconds,
            # Report the integer sustained-seconds at emit time (>= the window).
            sustained_seconds=int(sustained),
            sync_error_count=sample.sync_error_count,
        )

    return DetectorResult(
        new_state=DetectorState(
            lag_onset_epoch=onset,
            emitted=already_emitted or should_emit,
            last_sync_count=sample.sync_count,
            last_sync_error_count=sample.sync_error_count,
            onset_sync_error_count=onset_error_count,
        ),
        should_emit=should_emit,
        emit_fields=emit_fields,
    )


def state_from_json(raw: dict[str, Any]) -> DetectorState:
    """Coerce a loaded JSON dict into a :class:`DetectorState`.

    Tolerant of an absent/old shape: missing keys fall back to safe defaults
    (treat as "no current episode"). Raises ``ValueError`` only for grossly
    malformed types so a corrupted state file fails loudly rather than silently
    suppressing emissions forever.
    """
    onset = raw.get("lag_onset_epoch")
    # bool is a subclass of int — reject it explicitly so a stray `true` in the
    # state file can't become epoch 1.0 (review LOW).
    if onset is not None and (isinstance(onset, bool) or not isinstance(onset, (int, float))):
        raise ValueError(f"lag_onset_epoch must be a number or null, got {type(onset).__name__}")
    last_err = int(raw.get("last_sync_error_count", 0))
    return DetectorState(
        lag_onset_epoch=float(onset) if onset is not None else None,
        emitted=bool(raw.get("emitted", False)),
        last_sync_count=int(raw.get("last_sync_count", 0)),
        last_sync_error_count=last_err,
        # Story 13.4a — default to last_sync_error_count for state files written
        # before this field existed (an in-flight episode from an older version
        # then classifies as sync_stalled only if errors keep rising — safe).
        onset_sync_error_count=int(raw.get("onset_sync_error_count", last_err)),
    )
