"""GET /v1/health liveness probe (Story 11.3.7 / AC5 → Story 11.3.9 real signals).

Wire-shape contract MUST match
:class:`telegram_gateway.handlers.registry_client.HealthResponseLocal`
(declared at ``services/telegram-gateway/.../registry_client.py:82-112``):
``registry_status``, ``worker_status``, ``clawhip_queue_depth``, ``version``.

Story 11.3.9 replaced Story 11.3.7's static placeholder body with 3
parallel :mod:`registry_api.probes.health_probes` calls against the
existing read-only engine at ``app.state.session_maker`` (Story 2.9 +
Story 2.13 lifespan setup at ``app.py:191-200``). Per AC4 the 3 probes
run under :func:`asyncio.gather` with per-probe :func:`asyncio.wait_for`
budgets (200ms / 150ms / 150ms) so the route's total wall time is
``max(...)`` not ``sum(...)`` and any single hung probe degrades only
its own signal — the others still return their real values.

Per AC5 the route **NEVER** returns 5xx for a degraded backend: each
probe runs inside :func:`registry_api.probes.run_probe_with_budget`,
which catches ``asyncio.TimeoutError`` (from the budget wrapper),
``OperationalError`` (from sqlalchemy), and anything else, returning
``None`` instead of raising. Because every exception is swallowed at
that layer, the ``asyncio.gather`` call uses ``return_exceptions=False``
(it never actually sees an exception) and the route maps each ``None``
to its "degraded"/"unknown"/``0`` fallback in a 200 response. A second
defensive ``try/except`` around the whole ``session_maker()`` block
catches the one path the per-probe wrapper can't (the session factory
itself raising). The only way this route returns non-200 is a
panic-tier bug in the route body itself.

State vocabulary:

* ``registry_status``: ``"ok"`` (SELECT 1 returned) | ``"degraded"`` (error/timeout).
* ``worker_status``: ``"ok"`` (worker activity in window) | ``"idle"``
  (no activity, registry IS reachable) | ``"unknown"`` (registry NOT
  reachable — "we don't know" is safer than "idle" in that case).
* ``clawhip_queue_depth``: ``int`` (pending tasks in window) | ``0``
  (DB degraded — paired with ``registry_status="degraded"`` so the ``0``
  is not misread as "queue empty + healthy").

``HealthResponseLocal``'s permissive ``str`` typing (H1 note at
``registry_client.py:89-95``) accepts these new values verbatim without
a contract bump — see ``tests/contract/test_health_client_server_shape_parity.py``
for the formal enforcement.
"""

from __future__ import annotations

import asyncio
from typing import cast

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from registry_api._version import __version__
from registry_api.probes import (
    QUEUE_LOOKBACK_S_DEFAULT,
    WORKER_WINDOW_S_DEFAULT,
    probe_queue_depth,
    probe_registry_reachable,
    probe_worker_recently_active,
    run_probe_with_budget,
)

# Per-probe budgets (AC4). Sum is 500ms only if probes ran sequentially;
# running them in parallel via asyncio.gather makes the wall-clock budget
# = max(200, 150, 150) = 200ms in the happy path.
_REGISTRY_PROBE_BUDGET_S: float = 0.200
_WORKER_PROBE_BUDGET_S: float = 0.150
_QUEUE_PROBE_BUDGET_S: float = 0.150

# structlog binding — matches the pattern at `routes/digest.py` / `routes/tasks.py`.
# Plain ``structlog.get_logger`` returns a lazy proxy that accepts kwargs
# (`error_type=`, `probe=`, ...) regardless of whether structlog has been
# configured by the host application — unit tests don't run __main__.py's
# processor-chain setup, but the proxy still routes kwargs to the
# default stdlib logger via structlog's PrintLoggerFactory fallback.
_log = structlog.get_logger(__name__)


router = APIRouter()


class HealthResponse(BaseModel):
    """200 OK response body for GET /v1/health.

    Wire contract MUST match ``HealthResponseLocal`` in
    ``telegram_gateway/handlers/registry_client.py``: same field names +
    same Field constraints so the typed client parses without
    ``ValidationError``. The contract-parity test at
    ``tests/contract/test_health_client_server_shape_parity.py`` is the
    formal enforcement.
    """

    model_config = ConfigDict(frozen=True)

    # H1 (mirror of client constraint): str, not Literal — server contract
    # not yet finalised; if registry-api adds "warning"/"maintenance"/etc.
    # states, the client's permissive str typing forwards them verbatim.
    registry_status: str = Field(min_length=1, max_length=64)
    worker_status: str = Field(min_length=1, max_length=64)
    # L4: defensive upper bound prevents absurdly large queue depths.
    clawhip_queue_depth: int = Field(ge=0, le=1_000_000)
    # M11: defensive upper bound prevents overlong version strings exceeding
    # Telegram's 4096-char message limit when rendered in /ping reply.
    version: str = Field(min_length=1, max_length=200)


@router.get(
    "/health",
    status_code=200,
    response_model=HealthResponse,
    tags=["meta"],
)
async def get_health(request: Request) -> HealthResponse:
    """Liveness probe — 3 real signals via parallel read-only DB probes.

    Per AC5 the route NEVER returns 5xx for a degraded backend. Every
    failure mode below maps to a 200 with the appropriate fallback:

    * No ``session_maker`` on app.state (route mounted standalone in tests)
      → all 3 fields fall to degraded/unknown/0; ``version`` still real.
    * Session ``__aenter__`` raises → same as above.
    * Per-probe timeout / OperationalError → that probe's fallback only;
      the other 2 still return their real values.

    Returns:
        :class:`HealthResponse` — always 200, never 5xx.
    """
    session_maker: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, "session_maker", None
    )
    if session_maker is None:
        # Standalone test mount (no app.state) — degraded everything.
        # In production this branch should never fire because build_app
        # always wires session_maker (see app.py:197).
        _log.warning("v1_health_no_session_maker")
        return HealthResponse(
            registry_status="degraded",
            worker_status="unknown",
            clawhip_queue_depth=0,
            version=__version__,
        )

    # Probe windows come from the operator-tunable HealthProbeSettings
    # stashed on app.state by build_app (M1 fix — OMB_HEALTH_WORKER_WINDOW_S
    # / OMB_HEALTH_QUEUE_LOOKBACK_S, wired via pydantic-settings). Fall back
    # to the module defaults if the route is mounted standalone in tests
    # (no app.state.health_probe_settings).
    probe_settings = getattr(request.app.state, "health_probe_settings", None)
    worker_window_s = (
        probe_settings.worker_window_s if probe_settings is not None else WORKER_WINDOW_S_DEFAULT
    )
    queue_lookback_s = (
        probe_settings.queue_lookback_s if probe_settings is not None else QUEUE_LOOKBACK_S_DEFAULT
    )

    # Open ONE session, fan out 3 probes in parallel under that session.
    # aiosqlite + the registry-state read-only engine support concurrent
    # SELECTs on a single connection (no write contention). If a future
    # probe needs write isolation, switch to N parallel sessions.
    try:
        async with session_maker() as session:
            results = await asyncio.gather(
                run_probe_with_budget(
                    probe_registry_reachable(session),
                    budget_s=_REGISTRY_PROBE_BUDGET_S,
                    probe_name="registry",
                ),
                run_probe_with_budget(
                    probe_worker_recently_active(session, window_s=worker_window_s),
                    budget_s=_WORKER_PROBE_BUDGET_S,
                    probe_name="worker",
                ),
                run_probe_with_budget(
                    probe_queue_depth(session, lookback_s=queue_lookback_s),
                    budget_s=_QUEUE_PROBE_BUDGET_S,
                    probe_name="queue",
                ),
                return_exceptions=False,
            )
    except Exception as exc:  # noqa: BLE001 — degraded-on-anything (AC5)
        # session_maker() itself raised (engine disposed, connection-pool
        # exhausted, etc.) — degrade everything.
        _log.warning("v1_health_session_error", error_type=type(exc).__name__)
        return HealthResponse(
            registry_status="degraded",
            worker_status="unknown",
            clawhip_queue_depth=0,
            version=__version__,
        )

    registry_ok_raw, worker_active_raw, queue_depth_raw = results

    # AC1 mapping: True → "ok", False/None → "degraded".
    registry_ok = registry_ok_raw is True
    registry_status = "ok" if registry_ok else "degraded"

    # AC2 mapping: True → "ok" (worker activity in window); False AND
    # registry reachable → "idle" (cluster up, no work right now); None
    # (timeout/error) OR registry NOT reachable → "unknown" (we can't tell).
    if worker_active_raw is True:
        worker_status = "ok"
    elif worker_active_raw is False and registry_ok:
        worker_status = "idle"
    else:
        worker_status = "unknown"

    # AC3 mapping: degraded registry OR a failed queue probe (None) → 0
    # (paired with the degraded warning below so 0 is not misread as
    # "queue empty"). Otherwise the probe result (which may be 0
    # legitimately if the queue IS empty under a healthy registry).
    queue_probe_failed = queue_depth_raw is None
    clawhip_queue_depth = 0 if not registry_ok or queue_probe_failed else cast(int, queue_depth_raw)

    # Emit a degraded warning if ANY signal degraded: registry down,
    # worker probe couldn't tell, OR queue probe failed (H2 — a
    # queue-only failure must still surface in logs, otherwise the
    # rendered 0 looks identical to a healthy empty queue).
    if not registry_ok or worker_status == "unknown" or queue_probe_failed:
        _log.warning(
            "v1_health_degraded",
            registry_status=registry_status,
            worker_status=worker_status,
            clawhip_queue_depth=clawhip_queue_depth,
        )

    return HealthResponse(
        registry_status=registry_status,
        worker_status=worker_status,
        clawhip_queue_depth=clawhip_queue_depth,
        version=__version__,
    )
