"""Three read-only probes backing ``GET /v1/health`` (Story 11.3.9).

Each probe takes a SQLAlchemy ``AsyncSession`` (sourced from registry-api's
existing read-only engine at ``app.state.session_maker`` — see Story 2.9
``build_app`` / Story 2.13 lifespan setup at ``app.py:191-200``) and
returns a small typed value. Sessions are managed by the *caller* so the
probe layer stays free of lifecycle concerns; the route layer at
:mod:`registry_api.routes.health` opens one session, fans out the three
probes under :func:`asyncio.gather`, then closes the session.

Design constraints (from Story 11.3.9 Dev Notes):

* **READ-ONLY** — every probe is a ``SELECT`` against the materialised
  state. FR26 single-writer rule preserved (registry-state remains the
  sole writer; registry-api is a read-side consumer here).
* **NO new outbound HTTP dep** — registry-api does not call
  worker-wrapper or clawhip-daemon for liveness; signals are derived
  from registry-state's materialised tables. Preserves S-3 separability.
* **Best-effort error semantics** — every probe returns its
  "degraded"/"unknown"/``0`` fallback rather than raising on
  ``OperationalError`` / ``asyncio.TimeoutError``. The route layer
  layers the ``asyncio.wait_for`` budget on top; probes themselves do
  NOT impose timeouts (composability — a future caller might want a
  different budget).
* **No PII in any return value** — probes return primitives (bool, int).

Schema reference (`registry-state/src/registry_state/schema.py`):

* ``Task`` table: ``status`` column carries ``"pending"`` (post-task.created,
  pre-task.execution.started — set at handlers.py:169), ``"executing"``
  (post-task.execution.started — set at handlers.py:491), ``"blocked"``,
  ``"failed"``, plus the terminal ``"completed"``/``"cancelled"`` set
  elsewhere. AC3 queue-depth counts the pre-execution state ``"pending"``.
* ``Event`` table: ``type`` column for event categorisation, ``emitted_at``
  for time-windowed queries. AC2 worker-status filters
  ``task.execution.started`` / ``task.execution.completed`` /
  ``worker.heartbeat`` within ``window_s``.

Story 11.3.7 (the AC5 stop-gap) is the direct precedent for the
return-tuple shape consumed by the route layer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from registry_state.schema import (  # noqa: IMP001 — services→services allowed per AC-16 (mirrors app.py imports of EventLogWriter / sqlite_store)
    Event,
    Task,
)
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

# Default window for "is a worker active?" (AC2). 60s comfortably covers
# the typical worker heartbeat cadence + a missed beat without flipping
# the signal between "ok" and "idle" on noise. Env-overridable via
# ``OMB_HEALTH_WORKER_WINDOW_S`` (range 5-3600 — narrower than 5s is
# noisy, wider than 1h means "ok" is meaningless).
WORKER_WINDOW_S_DEFAULT: int = 60

# Default look-back for the queue-depth count (AC3). 300s covers the
# typical task pickup latency on a healthy cluster; pending tasks older
# than this are likely stuck (a separate, future "stuck task" alert
# should flag those). Env-overridable via ``OMB_HEALTH_QUEUE_LOOKBACK_S``.
QUEUE_LOOKBACK_S_DEFAULT: int = 300

# Wire-shape upper bound for queue depth (matches HealthResponseLocal's
# ``Field(ge=0, le=1_000_000)`` constraint at
# ``services/telegram-gateway/.../registry_client.py:109``). Probe-side
# clamp is defense-in-depth — even a runaway count gets clamped before
# the wire serialiser sees it.
QUEUE_DEPTH_MAX: int = 1_000_000

# Event types that signal "a worker is doing work right now" (AC2). The
# triplet is deliberate: ``task.execution.started`` + ``task.execution.completed``
# bracket every real execution, and ``worker.heartbeat`` lets an idle
# worker keep the signal alive between executions. If a future story
# introduces a 4th worker-side signal type, append here.
_WORKER_EVENT_TYPES: tuple[str, ...] = (
    "task.execution.started",
    "task.execution.completed",
    "worker.heartbeat",
)

# Task statuses that pre-date worker pickup (AC3 queue depth). Currently
# only ``"pending"`` per ``registry_state/domain/handlers.py:169``. If a
# future story adds an intermediate "queued" / "awaiting_resource" state,
# extend here so the queue-depth signal stays honest.
_PRE_EXECUTION_STATUSES: tuple[str, ...] = ("pending",)


async def probe_registry_reachable(session: AsyncSession) -> bool:
    """AC1 — is registry-state's SQLite store reachable for reads?

    Runs a cheap ``SELECT 1`` (no table dependency, no row scan). Returns
    ``True`` on success, ``False`` on ANY ``Exception`` (the caller maps
    ``False`` to ``registry_status="degraded"`` in a 200 response — per
    AC5 the route NEVER returns 5xx for a degraded backend).

    The bare ``except Exception`` is intentional: any sqlalchemy /
    aiosqlite / disk-IO surprise should degrade the signal, not propagate
    to the HTTP response. The caller's ``asyncio.wait_for`` layers a
    time budget on top.

    Args:
        session: open read-only :class:`AsyncSession`. Caller owns the
                 session lifecycle.

    Returns:
        ``True`` if the SELECT returned a row; ``False`` on any error.
    """
    try:
        result = await session.execute(text("SELECT 1"))
        # bool() coerces the Any from scalar_one() — mypy --strict no-any-return.
        return bool(result.scalar_one() == 1)
    except Exception:  # noqa: BLE001 — degraded-on-anything by design (AC1)
        return False


async def probe_worker_recently_active(
    session: AsyncSession,
    *,
    window_s: int = WORKER_WINDOW_S_DEFAULT,
    now: datetime | None = None,
) -> bool | None:
    """AC2 — was any worker active in the last ``window_s`` seconds?

    Counts events with ``type ∈ _WORKER_EVENT_TYPES`` and
    ``emitted_at >= now - window_s``. The ``LIMIT 1`` shape is critical —
    we do NOT need the exact count, just "any?". On a busy cluster this
    short-circuits at the first hit and avoids scanning the full events
    table.

    **Tri-state return** (code-review H1 fix): the probe distinguishes
    "no worker activity" (``False``) from "the query itself failed"
    (``None``). Collapsing both into ``False`` (the pre-fix behaviour)
    made the route's ``worker_status`` ladder mis-classify a BROKEN
    worker-probe (Event-table corruption while ``SELECT 1`` still
    succeeds) as ``"idle"`` — i.e. "cluster up, no work right now" — when
    the truth is "we can't tell". The route maps ``None`` to
    ``worker_status="unknown"`` (AC2).

    Args:
        session: open read-only :class:`AsyncSession`.
        window_s: look-back window in seconds. Default
                  :data:`WORKER_WINDOW_S_DEFAULT`. Caller is responsible
                  for clamping to a sane range (the env-var loader at
                  the settings layer clamps 5-3600).
        now: optional wall-clock anchor for the window (tests inject a
             frozen clock; production callers omit it → ``datetime.now(UTC)``).

    Returns:
        ``True`` if ≥1 worker event was found in the window;
        ``False`` if NONE found (the registry IS reachable, just no work);
        ``None`` if the query FAILED (caller maps to
        ``worker_status="unknown"`` — the safer "we couldn't tell" signal).
    """
    anchor = now if now is not None else datetime.now(UTC)
    cutoff = anchor - timedelta(seconds=window_s)
    try:
        stmt = (
            select(Event.id)
            .where(Event.type.in_(_WORKER_EVENT_TYPES))
            .where(Event.emitted_at >= cutoff)
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.first() is not None
    except Exception:  # noqa: BLE001 — degraded-on-anything by design (AC2)
        # Return None (NOT False) so the route distinguishes "no activity"
        # from "probe broke" — see tri-state docstring above (H1 fix).
        return None


async def probe_queue_depth(
    session: AsyncSession,
    *,
    lookback_s: int = QUEUE_LOOKBACK_S_DEFAULT,
    now: datetime | None = None,
) -> int | None:
    """AC3 — how many tasks are queued but not yet executing?

    Counts ``Task`` rows with ``status ∈ _PRE_EXECUTION_STATUSES`` (currently
    only ``"pending"``) whose ``created_at >= now - lookback_s``. The
    ``lookback_s`` window filter is important: pending tasks older than
    the look-back are almost certainly stuck (a queue-depth signal is
    meant to report "how much work is the system processing right now",
    not "how much accumulated debt exists" — that's a different alert).

    Result is clamped to ``[0, QUEUE_DEPTH_MAX]`` defense-in-depth so a
    runaway count from a corrupted index can't bypass ``HealthResponseLocal``
    field constraint at wire-serialise time.

    **Tri-state return** (code-review H2 fix): the probe distinguishes a
    legitimately-empty queue (``0``) from a FAILED query (``None``).
    Collapsing both into ``0`` (the pre-fix behaviour) let the route
    pair ``clawhip_queue_depth=0`` with ``registry_status="ok"`` when the
    Task index was broken but ``SELECT 1`` still succeeded — exactly the
    "queue empty + all healthy" misread this signal is meant to prevent.
    The route maps ``None`` to ``clawhip_queue_depth=0`` AND emits a
    degraded warning (AC3).

    Args:
        session: open read-only :class:`AsyncSession`.
        lookback_s: window in seconds; tasks created before this fall
                    out of the "active queue" signal.
        now: wall-clock anchor (tests inject; production omits).

    Returns:
        Number of pending tasks created within the window, clamped to
        ``[0, QUEUE_DEPTH_MAX]`` (``0`` is a legitimate empty-queue value);
        ``None`` if the query FAILED (caller pairs with
        ``registry_status="degraded"`` so the rendered ``0`` is not
        misread as "queue is empty + all healthy").
    """
    anchor = now if now is not None else datetime.now(UTC)
    cutoff = anchor - timedelta(seconds=lookback_s)
    try:
        stmt = (
            select(func.count(Task.id))
            .where(Task.status.in_(_PRE_EXECUTION_STATUSES))
            .where(Task.created_at >= cutoff)
        )
        result = await session.execute(stmt)
        raw = result.scalar_one()
        # Defense-in-depth clamp matches HealthResponseLocal Field(ge=0, le=1_000_000)
        # at telegram-gateway/.../registry_client.py:109. Negative values
        # are impossible from COUNT() but the clamp is cheap and bullet-proof.
        return max(0, min(int(raw), QUEUE_DEPTH_MAX))
    except Exception:  # noqa: BLE001 — degraded-on-anything by design (AC3)
        # Return None (NOT 0) so the route distinguishes "empty queue"
        # from "probe broke" — see tri-state docstring above (H2 fix).
        return None
