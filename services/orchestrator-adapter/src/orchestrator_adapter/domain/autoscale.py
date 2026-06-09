"""Poll-based worker pool auto-scaler (FC-P6-1 / Story P8-FC2).

Queries the task queue depth via the task-registry MCP and adjusts
worker-wrapper replica count.  All scaling operations emit ``pool.scaled``
events.  Disabled when ``autoscale_enabled=False`` (default).

The controller is fully unit-testable without Docker, MCP, or event writers
through injectable dependencies (``log``, ``event_writer``, ``clock``).
Actual ``docker compose`` execution is deferred to Story P8-FC3; this module
logs the would-be command instead.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import structlog

if TYPE_CHECKING:
    from events.clock import Clock
    from events.event_log_writer import EventLogWriter

from events.envelope import Actor, EventEnvelope
from events.ids import new_event_id, new_idempotency_key, new_uuid7
from events.payloads import PoolScaledPayload

from orchestrator_adapter.app.config import OrchestratorSettings

# Statuses that indicate a worker is actively processing a task.
_ACTIVE_STATUSES: frozenset[str] = frozenset({"executing", "planning", "plan_ready"})

# Minimum consecutive polls with idle excess before scaling down.
_SCALE_DOWN_CONSECUTIVE_POLLS: int = 2


@runtime_checkable
class McpClientGroupProto(Protocol):
    """Minimal protocol consumed by :class:`AutoscaleController`.

    Only the ``task_registry`` attribute is required; the controller never
    touches session-registry or clawhip-bridge directly.
    """

    task_registry: object | None


class AutoscaleController:
    """Poll-based worker pool auto-scaler (FC-P6-1).

    Queries the task queue depth via the task-registry MCP and adjusts
    worker-wrapper replica count via ``docker compose up --scale``.
    All scaling operations emit ``pool.scaled`` events.
    Disabled when ``autoscale_enabled=False`` (default).
    """

    def __init__(
        self,
        settings: OrchestratorSettings,
        *,
        log: structlog.stdlib.BoundLogger | None = None,
        event_writer: EventLogWriter | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._settings = settings
        self._log = log or structlog.get_logger(__name__)
        self._event_writer = event_writer
        self._clock = clock
        self._current_count: int = settings.autoscale_min
        self._last_scale_time: float = 0.0
        self._idle_excess_count: int = 0  # consecutive polls with idle excess

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def current_count(self) -> int:
        """Current tracked worker count (for testing / observability)."""
        return self._current_count

    async def poll(self, mcp_client_group: McpClientGroupProto) -> None:  # noqa: ARG002
        """Run one scaling decision cycle.

        If ``autoscale_enabled`` is ``False``, returns immediately (no-op).

        Parameters
        ----------
        mcp_client_group:
            The MCP client group providing access to the task-registry.
            Currently accepted but not yet queried — the real MCP integration
            lands in Story P8-FC3.  For now, the controller uses injected
            ``_query_pending_count`` / ``_query_idle_count`` callables in
            tests and defaults to zero counts in production until wiring.
        """
        if not self._settings.autoscale_enabled:
            return

        pending_count = await self._query_pending_count()
        idle_count = await self._query_idle_count()

        if (
            pending_count > self._settings.autoscale_up_threshold
            and self._current_count < self._settings.autoscale_max
        ):
            await self._scale_to(
                self._current_count + 1,
                "queue_depth_exceeded",
            )
        elif idle_count > self._settings.autoscale_down_threshold:
            self._idle_excess_count += 1
            if self._idle_excess_count >= _SCALE_DOWN_CONSECUTIVE_POLLS:
                await self._scale_to(
                    self._current_count - 1,
                    "idle_workers_exceeded",
                )
                self._idle_excess_count = 0
        else:
            self._idle_excess_count = 0

    # ------------------------------------------------------------------
    # Override points (for testing / future wiring)
    # ------------------------------------------------------------------

    async def _query_pending_count(self) -> int:
        """Query the number of pending tasks. Override in tests or P8-FC3 wiring."""
        return 0

    async def _query_idle_count(self) -> int:
        """Query the number of idle workers. Override in tests or P8-FC3 wiring."""
        return 0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _scale_to(self, target_count: int, reason: str) -> None:
        """Scale the worker pool to *target_count*, enforcing bounds and cooldown.

        Emits a ``pool.scaled`` event on success.
        """
        # Clamp to min/max bounds.
        target_count = max(
            self._settings.autoscale_min,
            min(self._settings.autoscale_max, target_count),
        )

        # No-op if target matches current.
        if target_count == self._current_count:
            return

        # Enforce cooldown: skip if last scale was within poll_interval_s.
        now = time.monotonic()
        elapsed = now - self._last_scale_time
        if self._last_scale_time > 0 and elapsed < self._settings.autoscale_poll_interval_s:
            self._log.debug(
                "autoscale_cooldown_active",
                elapsed_s=round(elapsed, 2),
                cooldown_s=self._settings.autoscale_poll_interval_s,
            )
            return

        old_count = self._current_count

        # Execute scale command (log-only until P8-FC3 wires Docker).
        self._execute_scale_command(target_count)

        self._current_count = target_count
        self._last_scale_time = time.monotonic()

        self._log.info(
            "autoscale_pool_scaled",
            old_count=old_count,
            new_count=target_count,
            reason=reason,
        )

        # Emit pool.scaled event.
        await self._emit_pool_scaled_event(old_count, target_count, reason)

    def _execute_scale_command(self, target_count: int) -> None:
        """Log the scale command (Docker execution deferred to P8-FC3)."""
        self._log.info(
            "autoscale_would_execute",
            command=(
                f"docker compose up -d "
                f"--scale worker-wrapper={target_count} "
                f"--no-deps worker-wrapper"
            ),
            target_count=target_count,
        )

    async def _emit_pool_scaled_event(self, old_count: int, new_count: int, reason: str) -> None:
        """Emit a ``pool.scaled`` event via the event writer (if available)."""
        if self._event_writer is None or self._clock is None:
            return

        payload = PoolScaledPayload(
            old_count=old_count,
            new_count=new_count,
            trigger_reason=reason,
        )
        envelope = EventEnvelope(
            event_id=new_event_id(clock=self._clock),
            type="pool.scaled",
            emitted_at=self._clock.now(),
            emitted_at_monotonic_ns=self._clock.monotonic_ns(),
            actor=Actor(kind="system", id="autoscale-controller"),
            payload=payload.model_dump(),
            trace_id=new_uuid7(clock=self._clock),
            request_id=new_idempotency_key(clock=self._clock),
            schema_version="1.1.0",
        )
        await self._event_writer.append(envelope)
