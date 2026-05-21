"""Story 11.5 / FR65a — HMAC signing key rotation detector.

At registry-api startup, compares the fingerprint of the current
``OPERATOR_HMAC_KEY`` against the last-known fingerprint persisted in
registry-state's ``KeyFingerprint`` table. If different (or absent on
first boot), emits exactly one ``key.rotated`` event recording the
transition.

Exactly-once semantics: fingerprint equality is the dedup invariant.
Re-running with the same key is a no-op — the detector's idempotent
side of the contract is enforced by registry-state's UPSERT
materializer (Story 11.5 AC3) + the emit guard here.

Story 11.5 PD1 — Event-log SSoT lookup (pass-1 review fix): the
detector now reads the most-recent ``key.rotated`` event from the
JSONL event log (the canonical SSoT per arch_refs P2-I3 derived
projection) BEFORE consulting registry-state's SQLite ``KeyFingerprint``
projection. This closes the cross-restart race where registry-api
restarts faster than the subscriber-materializer can process the
prior boot's ``key.rotated`` event: even if SQLite still shows the
stale prior fingerprint (or no row at all), the JSONL log has the
authoritative most-recent rotation. SQLite is only consulted as a
fallback for snapshot-restored deployments where the JSONL log has
been truncated/rotated out.

Synchronous + fail-loud (D3): rotation events MUST be persisted before
registry-api serves requests. Storage I/O failure during emission halts
startup with the underlying exception so operators can address the
deeper storage problem before any approval traffic flows. Same rationale
as Story 2.4's ``EventLogWriter.recover()`` being synchronous on startup
— audit invariant supersedes uptime.

D1 bootstrap sentinel: first boot with no prior ``KeyFingerprint`` row
(and no prior ``key.rotated`` in the JSONL log) emits ``key.rotated``
with ``previous_key_fingerprint = "0000000000000000"`` (16 zero-hex
chars; collision probability with a real ``SHA-256(key_bytes).hex()[:16]``
= 2⁻⁶⁴ — negligible for the single-operator key population). The
``previous != new`` invariant in ``KeyRotatedPayload`` therefore holds.

D4: ``actor_id = "key-rotation-detector"``. Distinguishes rotation
events from operator-driven approval events in audit logs without
polluting the env-var space.

FR26 single-writer invariant: this module writes to the event log
ONLY via ``EventLogWriter.append`` (the canonical append path). It does
NOT mutate the SQLite registry-state directly — the
``KeyFingerprint`` row is materialized by registry-state's subscriber.

NFR-S10 key isolation: structured logs may include the FINGERPRINT
(safe, one-way SHA-256 truncation) and the byte count, but NEVER the
key bytes themselves. ``compute_key_fingerprint`` (in ``packages/events``)
extracts ``key.get_secret_value()`` exactly once, frame-local.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from random import Random

from events.approval_signing import compute_key_fingerprint
from events.clock import Clock
from events.envelope import Actor, EventEnvelope
from events.ids import new_event_id, new_request_id, new_uuid7
from events.log_reader import read_log_lines
from events.payloads import KeyRotatedPayload
from pydantic import SecretStr
from registry_state.adapters.event_log import EventLogWriter  # noqa: IMP001 — services→services
from registry_state.schema import KeyFingerprint  # noqa: IMP001 — services→services
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Story 11.5 D1: 16 zero-hex chars reserved as the first-boot sentinel.
# Collision probability with a real ``SHA-256(key_bytes).hex()[:16]`` is 2⁻⁶⁴,
# which is negligible for the single-operator key population this Platform
# serves. Documented in ``KeyRotatedPayload`` docstring + ADR-0006.
_BOOTSTRAP_FINGERPRINT_SENTINEL = "0000000000000000"

# Story 11.5 D4: explicit service identifier in the rotation audit event.
# ``KeyRotatedPayload.actor_id`` is constrained to ``min_length=1`` (D3 in
# Story 11.2) so this string value is accepted at the payload boundary.
_ROTATION_DETECTOR_ACTOR_ID = "key-rotation-detector"

_log = logging.getLogger("registry_api.adapters.key_rotation")


def _read_most_recent_rotation_fp_from_log(log_dir: Path) -> str | None:
    """Story 11.5 PD1 — return the most-recent ``key.rotated``'s
    ``new_key_fingerprint`` by scanning the JSONL event log most-recent
    day backward.

    Implements the event-log-first dedup pattern: the JSONL log is the
    SSoT per arch_refs P2-I3 derived projection. The detector consults
    this BEFORE the SQLite ``KeyFingerprint`` projection so a fast
    cross-restart cannot cause duplicate ``key.rotated`` emissions when
    the subscriber-materializer has not yet processed the prior boot's
    event.

    Args:
        log_dir: Root directory containing per-day ``YYYY-MM-DD.jsonl``
            event log files. May not exist (first-boot deployments).

    Returns:
        The ``new_key_fingerprint`` of the most-recent ``key.rotated``
        event, or ``None`` if no such event is present in the log
        (or the log directory does not exist).
    """
    if not log_dir.exists() or not log_dir.is_dir():
        return None
    # ``YYYY-MM-DD.jsonl`` sort order is chronological because the file
    # name format is ISO-8601 lexicographic. Walk most-recent day first
    # so we can return after the first hit on a day.
    jsonl_files = sorted(log_dir.glob("*.jsonl"), reverse=True)
    for path in jsonl_files:
        most_recent_fp: str | None = None
        try:
            for envelope in read_log_lines(path):
                if envelope.type == "key.rotated":
                    payload = envelope.payload
                    if isinstance(payload, KeyRotatedPayload):
                        most_recent_fp = payload.new_key_fingerprint
                    elif isinstance(payload, dict):
                        new_fp = payload.get("new_key_fingerprint")
                        if isinstance(new_fp, str):
                            most_recent_fp = new_fp
        except (OSError, FileNotFoundError):
            # The file may be racing with a writer that just rolled the
            # day; treat as no hit for this file and continue.
            continue
        if most_recent_fp is not None:
            return most_recent_fp
    return None


async def _read_prior_fingerprint(
    session_maker: async_sessionmaker[AsyncSession],
    log_dir: Path,
) -> str | None:
    """Story 11.5 PD1 — read the prior fingerprint with event-log-first
    semantics, falling back to SQLite when the log has no rotation
    events.

    The JSONL log is the SSoT (arch_refs P2-I3). SQLite's
    ``KeyFingerprint`` row is a derived projection that the
    subscriber-materializer maintains and may lag behind the log
    across a fast cross-restart. Consulting the log first closes the
    duplicate-emission window. SQLite remains the fallback for
    snapshot-restored deployments where the JSONL log was truncated.
    """
    log_fp = _read_most_recent_rotation_fp_from_log(log_dir)
    if log_fp is not None:
        return log_fp
    async with session_maker() as session:
        result = await session.execute(select(KeyFingerprint).where(KeyFingerprint.id == "current"))
        row = result.scalar_one_or_none()
    return row.fingerprint if row is not None else None


async def detect_and_emit_key_rotation(
    *,
    current_key: SecretStr | None,
    session_maker: async_sessionmaker[AsyncSession],
    event_log_writer: EventLogWriter,
    clock: Clock,
    actor_id: str = _ROTATION_DETECTOR_ACTOR_ID,
    rng: Random | None = None,
) -> None:
    """Detect HMAC key rotation at startup; emit ``key.rotated`` if changed.

    Reads the last-known fingerprint from the JSONL event log first
    (PD1 — SSoT per arch_refs P2-I3) and falls back to registry-state's
    ``KeyFingerprint`` singleton row only when the log has no
    ``key.rotated`` events (snapshot-restored deployments).

    Cases:

    * ``current_key is None``: signing is disabled (operator deliberately
      unset ``OPERATOR_HMAC_KEY``). No-op + structured log noting the
      skip. Pre-existing rotation events in the event log remain
      verifiable by archived keys via ``just verify-approval --key-file``.
      PP3: if a prior fingerprint EXISTS in the audit log, the skip is
      promoted to WARNING so operators notice an inconsistent state.
    * No prior fingerprint AND ``current_key`` set: FIRST BOOT WITH KEY.
      Emit ``key.rotated`` with ``previous_key_fingerprint =
      _BOOTSTRAP_FINGERPRINT_SENTINEL`` and ``new_key_fingerprint =
      <current_fp>``. The sentinel preserves
      ``KeyRotatedPayload.previous != new`` (D1).
    * Prior fingerprint == current fingerprint: no rotation. No-op.
    * Prior fingerprint != current fingerprint: ROTATION DETECTED.
      Emit ``key.rotated`` with ``previous=<prior_fp>``,
      ``new=<current_fp>``.

    Exactly-once invariant: PD1 — the event log is the SSoT; reading
    the most-recent ``key.rotated`` event from the log before consulting
    SQLite closes the cross-restart race where a fast restart could
    re-emit a rotation that the subscriber-materializer had not yet
    written to SQLite.

    Fail-loud (D3): any exception during fingerprint comparison or event
    emission propagates to the caller, which halts FastAPI's lifespan
    startup. Operators must address the storage / write problem before
    serving any approval traffic.

    Args:
        current_key: The current operator HMAC signing key (or ``None``
            when ``OPERATOR_HMAC_KEY`` is unset). Wrapped in
            :class:`SecretStr` so the raw bytes never appear in logs.
        session_maker: Read-side ``async_sessionmaker`` against the
            registry-state SQLite store (created by registry-api's
            lifespan). Used as a fallback when the JSONL log lacks any
            ``key.rotated`` events.
        event_log_writer: The shared :class:`EventLogWriter` instance.
            FR26 single-writer invariant: this is the ONLY mutation path
            for the event log — never writes SQLite directly. The
            writer's ``base_dir`` doubles as the log-scan root for the
            PD1 event-log dedup read path.
        clock: Injectable :class:`Clock` for emitted timestamps. Tests
            inject :class:`events.FrozenClock`.
        actor_id: Identity stamped on the emitted ``key.rotated`` event's
            ``actor_id`` payload field (D4 default;
            ``_ROTATION_DETECTOR_ACTOR_ID``).
        rng: PP7 — optional injectable :class:`random.Random` for
            deterministic test verification of event-id uniqueness. When
            ``None`` (default), a per-invocation RNG seeded from
            ``clock.monotonic_ns() ^ hash(os.urandom(8))`` is used so
            consecutive same-FrozenClock calls produce DIFFERENT
            event_ids (was an identical-id correctness defect pre-PP7).
    """
    # PD1: the JSONL log directory is the SSoT lookup root. The writer's
    # ``_base_dir`` is the canonical path — we read it on entry so a
    # potential writer-rotation between attribute read and log scan
    # cannot move the target underneath us.
    log_dir = event_log_writer._base_dir  # noqa: SLF001 — PD1 SSoT lookup

    if current_key is None:
        # PP3: if a prior fingerprint exists (in log or SQLite), the
        # operator has previously rotated keys — leaving signing disabled
        # is an inconsistent-state condition worth surfacing as WARNING.
        prior_fp_for_warning = await _read_prior_fingerprint(session_maker, log_dir)
        if prior_fp_for_warning is not None:
            _log.warning(
                "key_rotation_detection_skipped_with_prior_key "
                "prior_fp_exists=True prior_fingerprint=%s "
                "reason=OPERATOR_HMAC_KEY_unset actor_id=%s",
                prior_fp_for_warning,
                actor_id,
            )
        else:
            _log.info(
                "key_rotation_detection_skipped "
                "reason=OPERATOR_HMAC_KEY_unset_no_prior_key actor_id=%s",
                actor_id,
            )
        return

    current_fp = compute_key_fingerprint(current_key)

    # PD1: read the prior fingerprint with event-log-first semantics.
    # The JSONL log is the SSoT; SQLite is the fallback for snapshot-
    # restored deployments where the log has been truncated/rotated out.
    prior_fp = await _read_prior_fingerprint(session_maker, log_dir)

    if prior_fp == current_fp:
        # No-op: same key in effect as last boot.
        _log.info(
            "key_rotation_no_op fingerprint=%s actor_id=%s",
            current_fp,
            actor_id,
        )
        return

    # Rotation detected (or first boot). Build + emit the event.
    previous_fp = prior_fp if prior_fp is not None else _BOOTSTRAP_FINGERPRINT_SENTINEL
    rotated_at = clock.now()
    payload = KeyRotatedPayload(
        previous_key_fingerprint=previous_fp,
        new_key_fingerprint=current_fp,
        rotated_at=rotated_at,
        actor_id=actor_id,
    )

    # PP7: deterministic-but-distinct IDs scoped to this single emission.
    # Pre-PP7 `Random(clock.monotonic_ns())` produced IDENTICAL event_ids
    # for two same-FrozenClock invocations. Mix per-invocation entropy
    # (``os.urandom(8)``'s hash) so consecutive calls under one
    # FrozenClock yield distinct event_ids. Callers may also inject a
    # deterministic RNG via the ``rng`` parameter for test reproducibility.
    if rng is None:
        rng = Random(clock.monotonic_ns() ^ hash(os.urandom(8)))
    event_id = new_event_id(clock=clock, rng=rng)
    trace_id = new_uuid7(clock=clock, rng=rng)
    request_id = new_request_id(clock=clock, rng=rng)

    envelope = EventEnvelope.create(
        event_id=event_id,
        type="key.rotated",
        schema_version="1.1.0",
        emitted_at=rotated_at,
        emitted_at_monotonic_ns=clock.monotonic_ns(),
        actor=Actor(kind="system", id=actor_id),
        payload=payload,
        trace_id=trace_id,
        request_id=request_id,
    )
    await event_log_writer.append(envelope)
    _log.info(
        "key_rotation_emitted previous_fingerprint=%s new_fingerprint=%s "
        "is_bootstrap=%s actor_id=%s",
        previous_fp,
        current_fp,
        prior_fp is None,
        actor_id,
    )


__all__ = ["detect_and_emit_key_rotation"]
