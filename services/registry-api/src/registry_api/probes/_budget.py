"""Probe budget wrapper (Story 11.3.9 / AC4 + code-review L1).

Generic ``asyncio.wait_for`` orchestration extracted from
``routes/health.py`` so a future ``/v1/ready`` route (signposted in
:mod:`registry_api.probes`'s package docstring) reuses the SAME
timeout-and-log-on-degrade helper instead of copy-pasting it across
route modules (the L1 altitude finding: the helper is probe-orchestration
logic, not route-specific wire-shaping).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import structlog

# structlog binding — matches routes/digest.py / routes/tasks.py. The lazy
# proxy accepts kwargs even when structlog is unconfigured (unit tests
# don't run __main__.py's processor-chain setup).
_log = structlog.get_logger(__name__)


async def run_probe_with_budget(
    probe_awaitable: Awaitable[object],
    *,
    budget_s: float,
    probe_name: str,
) -> object | None:
    """Run a probe awaitable under an :func:`asyncio.wait_for` budget.

    Returns ``None`` on ``TimeoutError`` or any other exception, after
    logging a structured warning. The caller decides how ``None`` maps
    to the wire-shape fallback (degraded/unknown/0) per AC1-AC3.

    Separating the budget wrapper from the probe body keeps probes
    composable — a future ``/v1/ready`` route may want different
    budgets without rewriting the probes (L1 altitude fix: lives in the
    probes package, not in routes/health.py).

    Args:
        probe_awaitable: the probe coroutine (e.g. ``probe_registry_reachable(session)``).
                         Typed ``Awaitable[object]`` — code-review M2 fix;
                         the prior ``asyncio.Future[object]`` annotation was
                         wrong (callers pass native coroutines, not Futures;
                         ``wait_for`` accepts both at runtime but strict
                         type-checkers flag the call sites).
        budget_s: per-probe timeout in seconds.
        probe_name: label for the structured-log warning on degrade.

    Returns:
        The probe's result on success; ``None`` on timeout or any error.
    """
    try:
        return await asyncio.wait_for(probe_awaitable, timeout=budget_s)
    except TimeoutError:
        _log.warning("v1_health_probe_timeout", probe=probe_name, budget_s=budget_s)
        return None
    except Exception as exc:  # noqa: BLE001 — degraded-on-anything (AC5)
        _log.warning(
            "v1_health_probe_error",
            probe=probe_name,
            error_type=type(exc).__name__,
        )
        return None
