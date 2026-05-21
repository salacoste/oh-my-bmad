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

Synchronous + fail-loud (D3): rotation events MUST be persisted before
registry-api serves requests. Storage I/O failure during emission halts
startup with the underlying exception so operators can address the
deeper storage problem before any approval traffic flows. Same rationale
as Story 2.4's ``EventLogWriter.recover()`` being synchronous on startup
— audit invariant supersedes uptime.

D1 bootstrap sentinel: first boot with no prior ``KeyFingerprint`` row
emits ``key.rotated`` with ``previous_key_fingerprint =
"0000000000000000"`` (16 zero-hex chars; collision probability with a
real ``SHA-256(key)[:8]`` = 2⁻⁶⁴ — negligible for the single-operator
key population). The ``previous != new`` invariant in
``KeyRotatedPayload`` therefore holds.

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
from random import Random

from events.approval_signing import compute_key_fingerprint
from events.clock import Clock
from events.envelope import Actor, EventEnvelope
from events.ids import new_event_id, new_request_id, new_uuid7
from events.payloads import KeyRotatedPayload
from pydantic import SecretStr
from registry_state.adapters.event_log import EventLogWriter  # noqa: IMP001 — services→services
from registry_state.schema import KeyFingerprint  # noqa: IMP001 — services→services
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Story 11.5 D1: 16 zero-hex chars reserved as the first-boot sentinel.
# Collision probability with a real ``SHA-256(key_bytes)[:8]`` is 2⁻⁶⁴,
# which is negligible for the single-operator key population this Platform
# serves. Documented in ``KeyRotatedPayload`` docstring + ADR-0006.
_BOOTSTRAP_FINGERPRINT_SENTINEL = "0000000000000000"

# Story 11.5 D4: explicit service identifier in the rotation audit event.
# ``KeyRotatedPayload.actor_id`` is constrained to ``min_length=1`` (D3 in
# Story 11.2) so this string value is accepted at the payload boundary.
_ROTATION_DETECTOR_ACTOR_ID = "key-rotation-detector"

_log = logging.getLogger("registry_api.adapters.key_rotation")


async def detect_and_emit_key_rotation(
    *,
    current_key: SecretStr | None,
    session_maker: async_sessionmaker[AsyncSession],
    event_log_writer: EventLogWriter,
    clock: Clock,
    actor_id: str = _ROTATION_DETECTOR_ACTOR_ID,
) -> None:
    """Detect HMAC key rotation at startup; emit ``key.rotated`` if changed.

    Reads the last-known fingerprint from registry-state's
    ``KeyFingerprint`` table (singleton row keyed on ``id="current"``).
    Compares against the fingerprint of the supplied ``current_key``.

    Cases:

    * ``current_key is None``: signing is disabled (operator deliberately
      unset ``OPERATOR_HMAC_KEY``). No-op + structured log noting the
      skip. Pre-existing rotation events in the event log remain
      verifiable by archived keys via ``just verify-approval --key-file``.
    * No prior fingerprint AND ``current_key`` set: FIRST BOOT WITH KEY.
      Emit ``key.rotated`` with ``previous_key_fingerprint =
      _BOOTSTRAP_FINGERPRINT_SENTINEL`` and ``new_key_fingerprint =
      <current_fp>``. The sentinel preserves
      ``KeyRotatedPayload.previous != new`` (D1).
    * Prior fingerprint == current fingerprint: no rotation. No-op.
    * Prior fingerprint != current fingerprint: ROTATION DETECTED.
      Emit ``key.rotated`` with ``previous=<prior_fp>``,
      ``new=<current_fp>``.

    Exactly-once invariant: after this function returns and registry-state
    materializes the event, the singleton ``KeyFingerprint`` row reflects
    the current key. Crashes between event emit and materialization are
    recovered via standard event-log replay (registry-state's subscriber
    re-processes the missing event on next boot).

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
            lifespan). Used to fetch the prior ``KeyFingerprint`` row.
        event_log_writer: The shared :class:`EventLogWriter` instance.
            FR26 single-writer invariant: this is the ONLY mutation path
            for the event log — never writes SQLite directly.
        clock: Injectable :class:`Clock` for emitted timestamps. Tests
            inject :class:`events.FrozenClock`.
        actor_id: Identity stamped on the emitted ``key.rotated`` event's
            ``actor_id`` payload field (D4 default;
            ``_ROTATION_DETECTOR_ACTOR_ID``).
    """
    if current_key is None:
        _log.info(
            "key_rotation_detection_skipped reason=OPERATOR_HMAC_KEY_unset actor_id=%s",
            actor_id,
        )
        return

    current_fp = compute_key_fingerprint(current_key)

    # Read last-known fingerprint from registry-state (None on first boot).
    async with session_maker() as session:
        result = await session.execute(select(KeyFingerprint).where(KeyFingerprint.id == "current"))
        row = result.scalar_one_or_none()
        prior_fp: str | None = row.fingerprint if row is not None else None

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

    # Deterministic IDs scoped to this single emission. We use a fresh RNG
    # seeded from the clock's monotonic counter so the IDs are unique per
    # boot. The trace_id is a UUIDv7 minted from the same source.
    rng = Random(clock.monotonic_ns())
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
